import time
import signal
import threading
from main import _stop_event, sigterm_handler

def test_stop_event_early_wake_up():
    """
    Test 1: Chứng minh cơ chế "bừng tỉnh sớm" của ngủ động.
    _stop_event.wait(timeout=10) phải phản hồi ngay lập tức (< 1 giây) khi thread khác bật cờ.
    """
    # Khởi tạo trạng thái sạch cho môi trường độc lập
    _stop_event.clear()
    start_time = time.time()
    
    # Tạo một luồng phụ giả lập OS kích hoạt sự kiện tắt sau 0.1 giây ngắn ngủi
    def simulate_concurrent_signal():
        time.sleep(0.1)
        _stop_event.set()
        
    worker_thread = threading.Thread(target=simulate_concurrent_signal)
    worker_thread.start()
    
    # Lệnh block luồng chính yêu cầu ngủ 10 giây
    returned_status = _stop_event.wait(timeout=10)
    execution_duration = time.time() - start_time
    
    worker_thread.join()
    
    # Xác thực: Trả về True ngay lập tức và thời gian xử lý bắt buộc nhỏ hơn 1 giây
    assert returned_status is True
    assert execution_duration < 1.0, f"Lỗi! Tiến trình bị block lâu quá mức kỳ vọng: {execution_duration}s"
    
    # Thu dọn bãi chiến trường sau khi test xong
    _stop_event.clear()


def test_sigterm_handler_sets_event():
    """
    Test 2: Kiểm chứng tính đơn nhiệm nguyên tử của sigterm_handler.
    Sau khi thực thi, cờ hiệu _stop_event.is_set() bắt buộc phải chuyển sang True.
    """
    # Khởi tạo trạng thái sạch
    _stop_event.clear()
    assert _stop_event.is_set() is False
    
    # Gọi trực tiếp handler mô phỏng cú bắn tín hiệu từ Kernel
    sigterm_handler(signal.SIGTERM, None)
    
    # Xác thực: Cờ hiệu đã chuyển trạng thái thành công
    assert _stop_event.is_set() is True
    
    # Thu dọn bãi chiến trường sau khi test xong
    _stop_event.clear()
