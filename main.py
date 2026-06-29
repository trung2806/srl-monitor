import json
import time
import logging
import sys
import signal
import asyncio
import asyncssh
import ipaddress
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Set, Tuple

# Import tầng xử lý logic và cấu trúc dữ liệu dùng chung
from srl_monitor import evaluate_metrics
from srl_cooldown import SystemAlertState, process_all_cooldowns
# Import hàm parse thuần túy và hằng số lệnh tập trung từ srl_fetcher
from srl_fetcher import CONTROL_CMD, parse_control_output

# Cấu hình log tập trung song song ra stdout và file ngoài
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("srl_monitor.log", encoding="utf-8")
    ]
)

# Outer Watchdog: lớn hơn tổng bộ đếm inner timeouts (3s connect + 3s cmd = 6s). Buffer 4s bảo toàn error taxonomy.
POLL_TIMEOUT_SECONDS = 10

# ALLOWLIST INFRA STATUSES: Chỉ intercept lọc bỏ nếu khớp chính xác các trạng thái lỗi hạ tầng dưới đây.
# Sử dụng frozenset nhằm đảm bảo tính bất biến (immutable) và tối ưu hóa lookup O(1).
_INFRA_ERROR_STATUSES = frozenset({"unreachable", "bad_data", "timeout", "error"})

# Hằng số cấu hình tương thích ngược mặc định cho hệ thống
DEFAULT_THRESHOLDS = {"cpu": 80, "memory": 25, "temperature": 75}


# ==============================================================================
# 0. TẦNG OBSERVABILITY (INTERNAL METRICS LAYER)
# ==============================================================================
@dataclass
class DaemonStats:
    """Container lưu trữ số liệu động phục vụ giám sát nội tại của Engine."""
    cycle_count: int = 0
    orphaned_cancel_count: int = 0
    sighup_count: int = 0
    timeout_node_count: int = 0
    alert_count: int = 0

# Khởi tạo instance duy nhất tại module-level để chia sẻ an toàn giữa Event Loop và Signal Handlers
STATS = DaemonStats()


# ==============================================================================
# 1. TẦNG CONFIGURATION LOADER (FAIL-FAST)
# ==============================================================================
def load_thresholds(filepath: str) -> Dict[str, int]:
    """Nạp và kiểm tra nghiêm ngặt tính hợp lệ của file cấu hình ngưỡng giám sát."""
    with open(filepath, "r", encoding="utf-8") as f:
        config = json.load(f)

    if not isinstance(config, dict):
        raise TypeError(f"Cấu hình gốc phải là một dictionary. Nhận được: {type(config).__name__}")

    required_keys = {"cpu", "memory", "temperature"}
    missing_keys = required_keys - config.keys()
    if missing_keys:
        raise ValueError(f"File cấu hình thiếu các key bắt buộc: {', '.join(sorted(missing_keys))}")
    validated_thresholds: Dict[str, int] = {}
    for key in required_keys:
        val = config[key]
        if not isinstance(val, int) or isinstance(val, bool):
            raise TypeError(f"Ngưỡng của '{key}' phải là số nguyên. Nhận được: {type(val).__name__}")
        if val <= 0:
            raise ValueError(f"Ngưỡng của '{key}' phải lớn hơn 0. Nhận được: {val}")
        validated_thresholds[key] = val

    return validated_thresholds


def load_nodes(filepath: str) -> List[str]:
    """Nạp và kiểm tra nghiêm ngặt danh sách IP thiết bị đầu vào từ file ngoài."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise TypeError(f"nodes.json phải là list. Nhận được: {type(data).__name__}")

    if not data:
        raise ValueError("Danh sách node không được rỗng.")

    for i, node in enumerate(data):
        if not isinstance(node, str):
            raise TypeError(f"Phần tử [{i}] phải là str. Nhận được: {type(node).__name__}")

        try:
            ipaddress.ip_address(node)
        except ValueError:
            raise ValueError(f"IP không hợp lệ tại [{i}]: '{node}'")

    seen = set()
    for node in data:
        if node in seen:
            raise ValueError(f"IP trùng lặp: '{node}'")
        seen.add(node)

    return data


# ==============================================================================
# 2. TẦNG TRANSPORT NETWORKING & PARSING (IO BOUNDARY)
# ==============================================================================
async def poll_node(host: str, username: str = "admin", password: str = "admin") -> Dict[str, Any]:
    """Mở kết nối SSH bất đồng bộ, chạy lệnh lấy dữ liệu thô và ép qua phễu lọc."""
    logging.info(f"🔌 [SSH] Đang kết nối tới node: {host}...")
    async with asyncssh.connect(
        host=host, username=username, password=password, known_hosts=None, connect_timeout=3
    ) as conn:
        logging.info(f"🚀 [SSH] Kết nối thành công! Đang chạy lệnh trên node: {host}...")
        result = await conn.run(CONTROL_CMD, timeout=3)
        logging.info(f"📄 [SSH] Nhận được output thô dài {len(result.stdout)} ký tự từ {host}")
        return parse_control_output(result.stdout)


async def safe_poll_node(host: str) -> Dict[str, Any]:
    """🧱 LAYER 1 ERROR BOUNDARY: Phân loại lỗi truyền dẫn (Transport) và lỗi cấu trúc dữ liệu (Data/Parse)."""
    try:
        return await poll_node(host)
    except (asyncssh.Error, OSError) as net_err:
        logging.error(f"❌ [TRANSPORT ERROR] Node {host} unreachable hoặc timeout mạng: {net_err}")
        return {
            "cpu": [{"index": "all", "total": {"average-1": 0}}],
            "memory": {"utilization": 0},
            "temperature": {"instant": 0, "alarm-status": False},
            "healthz": {"status": "unreachable", "reason": str(net_err)}
        }
    except (ValueError, TypeError, json.JSONDecodeError) as parse_err:
        logging.error(f"❌ [DATA PARSE ERROR] Node {host} trả về cấu trúc JSON bị hỏng hoặc thiếu: {parse_err}")
        return {
            "cpu": [{"index": "all", "total": {"average-1": 0}}],
            "memory": {"utilization": 0},
            "temperature": {"instant": 0, "alarm-status": False},
            "healthz": {"status": "bad_data", "reason": str(parse_err)}
        }
    except Exception as bug:
        logging.critical(f"💥 [BUG UNEXPECTED] safe_poll_node({host}): {bug}", exc_info=True)
        return {
            "cpu": [{"index": "all", "total": {"average-1": 0}}],
            "memory": {"utilization": 0},
            "temperature": {"instant": 0, "alarm-status": False},
            "healthz": {"status": "error", "reason": str(bug)}
        }


# ==============================================================================
# 2.5. MODULE-LEVEL TRANSPORT WRAPPER + FLEET RELOAD HELPER
# ==============================================================================
async def _wrapped_poll(host_str: str, timeout: float = POLL_TIMEOUT_SECONDS) -> Tuple[str, Dict[str, Any]]:
    """Outer watchdog cho safe_poll_node: bounds per-node với asyncio.wait_for, luôn trả (host, data)."""
    try:
        data = await asyncio.wait_for(safe_poll_node(host_str), timeout=timeout)
    except asyncio.TimeoutError:
        STATS.timeout_node_count += 1
        logging.warning(
            f"⏱️  [POLL TIMEOUT] Node {host_str} không phản hồi sau {timeout}s — dùng fallback timeout."
        )
        data = {
            "cpu": [{"index": "all", "total": {"average-1": 0}}],
            "memory": {"utilization": 0},
            "temperature": {"instant": 0, "alarm-status": False},
            "healthz": {"status": "timeout", "reason": f"poll exceeded {timeout}s"},
        }
    return host_str, data


def _apply_node_reload(
    new_nodes: List[str],
    current_registry: Dict[str, Any],
) -> Dict[str, Any]:
    """Pure function: merge new node list với current state registry."""
    return {h: current_registry.get(h, SystemAlertState()) for h in new_nodes}


# ==============================================================================
# 3. TẦNG OUTPUT CẢNH BÁO
# ==============================================================================
def emit_alert(host: str, alert: Dict[str, Any]) -> None:
    logging.info(f"🚨 [{host}] [{alert['event']}] Metric '{alert['metric']}' is {alert['status']}. Reason: {alert['reason']}")


# ==============================================================================
# 4. THE FUNCTIONAL CORE (PURE LOGIC)
# ==============================================================================
def tick(
    raw_data: Dict[str, Any],
    past_state: SystemAlertState,
    current_time: float,
    thresholds: Dict[str, int],
    cooldown_seconds: int
) -> Tuple[List[Dict[str, Any]], SystemAlertState]:
    analysis_result = evaluate_metrics(raw_data, thresholds)
    return process_all_cooldowns(
        analysis_result=analysis_result,
        current_state=past_state,
        current_time=current_time,
        cooldown_seconds=cooldown_seconds
    )


# ==============================================================================
# 5. THE IMPERATIVE SHELL (ASYNC EVENT LOOP)
# ==============================================================================
async def main_loop(
    interval_seconds: int = 2,
    cooldown_seconds: int = 1,
    config_path: str = "thresholds.json",
    nodes_path: str = "nodes.json",
    poll_timeout: float = POLL_TIMEOUT_SECONDS,
):
    """Vòng lặp chính điều phối xử lý theo mô hình Reactive và quản lý bẫy tín hiệu."""
    logging.info("🚀 Khởi động hệ thống giám sát SR Linux Monitor Fleet (Day 46)...")

    _stop_event = asyncio.Event()
    _reload_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    # SIGNAL TAXONOMY: Đăng ký định tuyến hành vi rõ ràng cho từng loại tín hiệu hệ thống
    loop.add_signal_handler(signal.SIGTERM, lambda: _stop_event.set())
    loop.add_signal_handler(signal.SIGINT, lambda: _stop_event.set())
    loop.add_signal_handler(signal.SIGHUP, lambda: _reload_event.set())

    # SIGUSR1 HANDLER: On-demand telemetry dump dạng Inline JSON phục vụ giám sát
    loop.add_signal_handler(
        signal.SIGUSR1,
        lambda: logging.info(f"📊 [STATS DUMP] {json.dumps(asdict(STATS))}")
    )

    try:
        current_thresholds = load_thresholds(config_path)
        nodes = load_nodes(nodes_path)
        logging.info(f"⚙️ Nạp cấu hình thành công từ '{config_path}': {current_thresholds}")
        logging.info(f"🖥️  Nạp danh sách fleet thành công từ '{nodes_path}': {nodes}")
    except Exception as err:
        logging.critical(f"💥 KHÔNG THỂ KHỞI ĐỘNG DAEMON: Lỗi cấu hình hoặc danh sách thiết bị: {err}")
        raise

    state_registry: Dict[str, SystemAlertState] = {host: SystemAlertState() for host in nodes}

    try:
        while not _stop_event.is_set():
            # --- SIGHUP: Hot-reload fleet list & thresholds ---
            if _reload_event.is_set():
                _reload_event.clear()
                STATS.sighup_count += 1
                try:
                    try:
                        current_thresholds = load_thresholds(config_path)
                    except Exception as config_err:
                        logging.error(f"❌ [SIGHUP] Nạp lại thresholds thất bại, giữ cấu hình cũ: {config_err}")

                    new_nodes = load_nodes(nodes_path)
                    state_registry = _apply_node_reload(new_nodes, state_registry)
                    nodes = new_nodes
                    logging.info(f"🔄 [SIGHUP] Fleet hot-reloaded thành công. Tổng nodes: {len(nodes)}")
                except Exception as err:
                    logging.error(f"❌ [SIGHUP] Reload danh sách node thất bại, giữ nguyên fleet cũ: {err}")

            STATS.cycle_count += 1
            logging.info(f"--- 🔄 Bắt đầu chu kỳ quét mới [#{STATS.cycle_count}] trên toàn bộ {len(nodes)} nodes ---")

            tasks: Set[asyncio.Task] = {asyncio.create_task(_wrapped_poll(host, poll_timeout)) for host in nodes}
            stop_task = asyncio.create_task(_stop_event.wait())
            tasks.add(stop_task)

            try:
                while len(tasks) > 1 and not _stop_event.is_set():
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

                    if stop_task in done:
                        logging.warning("🛑 [STOP] Nhận tín hiệu tắt hệ thống khi đang chờ dữ liệu mạng! Ngắt chu kỳ ngay lập tức.")
                        break

                    for fut in done:
                        # ==============================================================================
                        # GUARD CHECKLIST: KHÔNG ĐƯỢC DROP BLOCK NÀY TRONG CÁC LẦN REFACTOR TIẾP THEO
                        # 1. `fut is not stop_task`: identity check — tránh xử lý nhầm stop_task là network task.
                        # 2. `tasks.discard(fut)`: O(1) removal từ set — discard (không phải remove) để tránh
                        #    KeyError nếu invariant asyncio.wait bị vi phạm do bug trong tương lai.
                        # ==============================================================================
                        if fut is not stop_task:
                            tasks.discard(fut)

                            host, raw_data = fut.result()
                            now = time.time()

                            # INFRASTRUCTURE FILTER GUARD: Dùng allowlist để lọc lỗi, chặn dữ liệu hỏng
                            # lọt xuống core làm reset cooldown bừa bãi
                            healthz = raw_data.get("healthz") if isinstance(raw_data, dict) else None
                            if healthz and healthz.get("status") in _INFRA_ERROR_STATUSES:
                                logging.warning(
                                    f"⚠️ [{host}] Bỏ qua phân tích chu kỳ này do lỗi hạ tầng: "
                                    f"status='{healthz.get('status')}', reason='{healthz.get('reason', 'unknown')}'"
                                )
                                continue

                            # 🧱 LAYER 2 ERROR BOUNDARY: Gọi tầng Core xử lý logic thuần túy
                            try:
                                alerts, next_state = tick(
                                    raw_data=raw_data,
                                    past_state=state_registry[host],
                                    current_time=now,
                                    thresholds=current_thresholds,
                                    cooldown_seconds=cooldown_seconds,
                                )
                                state_registry[host] = next_state
                                if alerts:
                                    STATS.alert_count += len(alerts)
                                for alert in alerts:
                                    emit_alert(host, alert)
                            except Exception as bug:
                                logging.critical(
                                    f"💥 [LOGIC BUG] Lỗi xử lý dữ liệu cho node {host}: {bug}", exc_info=True
                                )
            finally:
                tasks.discard(stop_task)
                if not stop_task.done():
                    stop_task.cancel()
                    try:
                        await stop_task
                    except asyncio.CancelledError:
                        pass

                orphaned = [t for t in tasks if not t.done()]
                if orphaned:
                    STATS.orphaned_cancel_count += len(orphaned)
                    logging.info(f"🧹 Đang giải phóng {len(orphaned)} tác vụ mạng đang chạy ngầm...")
                for t in orphaned:
                    t.cancel()
                if orphaned:
                    await asyncio.gather(*orphaned, return_exceptions=True)

            if _stop_event.is_set():
                break

            # --- Reactive Sleep Phase ---
            sleep_task = asyncio.create_task(asyncio.sleep(interval_seconds))
            stop_task_sleep = asyncio.create_task(_stop_event.wait())
            reload_task = asyncio.create_task(_reload_event.wait())

            done_sleep, pending_sleep = await asyncio.wait(
                {sleep_task, stop_task_sleep, reload_task},
                return_when=asyncio.FIRST_COMPLETED
            )

            for t in pending_sleep:
                t.cancel()
            if pending_sleep:
                await asyncio.gather(*pending_sleep, return_exceptions=True)

            if _reload_event.is_set():
                logging.info("⚡ [SIGHUP] Phát hiện lệnh reload trong pha nghỉ. Ngắt sleep để thực thi ngay lập tức!")

    except asyncio.CancelledError:
        logging.warning("⚠️ Vòng lặp chính nhận tín hiệu hủy tác vụ.")
    finally:
        _stop_event.set()
        logging.info("🛑 SR Linux Monitor daemon đã dừng sạch sẽ. Exit code 0.")


if __name__ == "__main__":
    target_config = sys.argv[1] if len(sys.argv) > 1 else "thresholds.json"
    target_nodes = sys.argv[2] if len(sys.argv) > 2 else "nodes.json"
    try:
        asyncio.run(main_loop(config_path=target_config, nodes_path=target_nodes))
    except Exception:
        sys.exit(1)
