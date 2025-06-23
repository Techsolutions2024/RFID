# app.py (Fixed WebRTC Implementation)

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import serial
import threading
import time
from datetime import datetime
import sqlite3
import os
from contextlib import contextmanager
import cv2
import asyncio
import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaStreamTrack
import av
import uuid
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Flask và SocketIO Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'webrtc_rfid_secret_key'
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

# Dictionary để lưu các kết nối WebRTC
peer_connections = {}
camera_readers = {}

class CameraReader(threading.Thread):
    """Thread-safe camera reader để đọc frames từ camera/stream"""
    
    def __init__(self, url, direction, loop, ready_event):
        super().__init__(daemon=True)
        self.url = url
        self.direction = direction
        self.latest_frame = None
        self.is_running = True
        self.frame_lock = threading.Lock()
        self.cap = None
        self.loop = loop
        self.ready_event = ready_event
        
    def run(self):
        """Main loop để đọc frames từ camera"""
        retry_count = 0
        max_retries = 5
        
        logger.info(f"[{self.direction}] Bắt đầu đọc camera từ: {self.url}")
        
        while self.is_running and retry_count < max_retries:
            try:
                # Thử kết nối camera
                if self.cap is None or not self.cap.isOpened():
                    logger.info(f"[{self.direction}] Đang kết nối tới camera...")
                    
                    if self.url.isdigit():
                        camera_index = int(self.url)
                        self.cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
                    else:
                        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;udp"
                        self.cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)

                    if self.cap.isOpened():
                        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        logger.info(f"[{self.direction}] Kết nối camera thành công!")
                        
                        # Báo hiệu rằng camera đã sẵn sàng
                        self.loop.call_soon_threadsafe(self.ready_event.set)
                        retry_count = 0
                    else:
                        logger.error(f"[{self.direction}] Không thể kết nối camera")
                        retry_count += 1
                        time.sleep(2)
                        continue
                
                # Đọc frame
                ret, frame = self.cap.read()
                if ret and frame is not None:
                    with self.frame_lock:
                        self.latest_frame = frame.copy()
                    time.sleep(1/30)
                else:
                    logger.warning(f"[{self.direction}] Không đọc được frame từ camera. Thử kết nối lại...")
                    if self.cap:
                        self.cap.release()
                        self.cap = None
                    retry_count += 1
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"[{self.direction}] Lỗi camera reader: {e}")
                if self.cap:
                    self.cap.release()
                    self.cap = None
                retry_count += 1
                time.sleep(2)
        
        logger.warning(f"[{self.direction}] Camera reader dừng sau {retry_count} lần thử")
        self.cleanup()
    
    def get_frame(self):
        """Thread-safe lấy frame mới nhất"""
        with self.frame_lock:
            return self.latest_frame.copy() if self.latest_frame is not None else None
    
    def cleanup(self):
        """Dọn dẹp resources"""
        self.is_running = False
        if self.cap:
            self.cap.release()
            self.cap = None
        logger.info(f"[{self.direction}] Camera reader đã dọn dẹp")
    
    def stop(self):
        """Dừng camera reader"""
        self.cleanup()

class OpenCVVideoStreamTrack(MediaStreamTrack):
    """Custom video track cho aiortc từ OpenCV"""
    
    kind = "video"
    
    def __init__(self, camera_reader):
        super().__init__()
        self.camera_reader = camera_reader
        
    async def recv(self):
        """Nhận frame tiếp theo cho WebRTC stream"""
        pts, time_base = await self.next_timestamp()
        
        frame = self.camera_reader.get_frame()
        
        if frame is None:
            # Nếu không có frame, đợi một chút và tạo frame đen
            await asyncio.sleep(0.02)
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(frame, "No Signal", (250, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        av_frame = av.VideoFrame.from_ndarray(frame_rgb, format="rgb24")
        av_frame.pts = pts
        av_frame.time_base = time_base
        
        return av_frame

# --- RFID System Class (Giữ nguyên) ---
class RFIDControlSystem:
    def __init__(self):
        self.serial_connection = None
        self.is_connected = False
        self.is_running = False
        self.current_com_port = "COM3"
        self.current_baud_rate = 9600
        self.db_path = 'rfid_log.db'
        self.init_database()
        self.authorized_cards = set()
        self.load_authorized_cards()
        self.serial_thread = None
        self.auto_add_mode = False

    @contextmanager
    def get_db_connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()
    
    def init_database(self):
        with self.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS access_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    direction TEXT,
                    card_uid TEXT,
                    status TEXT
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS authorized_cards (
                    uid TEXT PRIMARY KEY,
                    name TEXT,
                    created_at TEXT
                )
            ''')
            conn.commit()
    
    def load_authorized_cards(self):
        with self.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT uid FROM authorized_cards')
            self.authorized_cards = {row[0] for row in cursor.fetchall()}
    
    def connect_arduino(self, com_port, baud_rate):
        try:
            if self.is_connected:
                self.disconnect_arduino()
            self.serial_connection = serial.Serial(com_port, baud_rate, timeout=1)
            self.is_connected = True
            self.is_running = True
            self.current_com_port = com_port
            self.current_baud_rate = baud_rate
            self.serial_thread = threading.Thread(target=self.read_serial_data, daemon=True)
            self.serial_thread.start()
            socketio.emit('connection_status', {'status': 'connected', 'message': f'Đã kết nối với Arduino trên {com_port}'})
            return True, f'Đã kết nối với Arduino trên {com_port}'
        except Exception as e:
            message = f'Không thể kết nối: {str(e)}'
            socketio.emit('connection_status', {'status': 'error', 'message': message})
            return False, message
    
    def disconnect_arduino(self):
        self.is_running = False
        self.is_connected = False
        if self.serial_connection:
            self.serial_connection.close()
            self.serial_connection = None
        socketio.emit('connection_status', {'status': 'disconnected', 'message': 'Đã ngắt kết nối với Arduino'})
    
    def read_serial_data(self):
        while self.is_running and self.is_connected:
            try:
                if self.serial_connection and self.serial_connection.in_waiting > 0:
                    line = self.serial_connection.readline().decode('utf-8').strip()
                    if line:
                        self.process_arduino_data(line)
                time.sleep(0.1)
            except Exception:
                self.disconnect_arduino()
                break
    
    def process_arduino_data(self, data):
        socketio.emit('log_message', {'message': f'Arduino: {data}'})
        if ":UID:" in data:
            try:
                parts = data.split(":UID:")
                direction = parts[0]
                uid = parts[1].replace(" ", "").upper()
                
                if self.auto_add_mode and direction == "IN" and uid not in self.authorized_cards:
                    default_name = f"Thẻ mới - {uid[:8]}"
                    self.add_card(uid, default_name)
                    response = "ALLOW"
                    status = "Cho phép"
                    socketio.emit('log_message', {'message': f'✨ Thẻ mới {uid} được tự động thêm và cho phép vào.'})
                    socketio.emit('access_granted', {'uid': uid, 'direction': direction})
                else:
                    if uid in self.authorized_cards:
                        response = "ALLOW"
                        status = "Cho phép"
                        socketio.emit('log_message', {'message': f'✓ Thẻ {uid} được phép {direction}'})
                        socketio.emit('access_granted', {'uid': uid, 'direction': direction})
                    else:
                        response = "DENY"
                        status = "Từ chối"
                        socketio.emit('log_message', {'message': f'✗ Thẻ {uid} không được phép {direction}'})
                        socketio.emit('access_denied', {'uid': uid, 'direction': direction})
                
                if self.serial_connection:
                    self.serial_connection.write((response + "\n").encode())
                
                self.save_access_log(direction, uid, status)
                socketio.emit('logs_updated', self.get_recent_logs())
            except Exception as e:
                socketio.emit('log_message', {'message': f'Lỗi xử lý dữ liệu: {str(e)}'})

    def save_access_log(self, direction, uid, status):
        with self.get_db_connection() as conn:
            cursor = conn.cursor()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute('INSERT INTO access_log (timestamp, direction, card_uid, status) VALUES (?, ?, ?, ?)',(timestamp, direction, uid, status))
            conn.commit()
    
    def add_card(self, uid, name):
        uid = uid.strip().upper()
        name = name.strip()
        if not uid or not name:
            return False, "Vui lòng nhập đầy đủ UID và tên"
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute('INSERT OR REPLACE INTO authorized_cards (uid, name, created_at) VALUES (?, ?, ?)',(uid, name, timestamp))
                conn.commit()
            self.authorized_cards.add(uid)
            socketio.emit('log_message', {'message': f'✓ Đã thêm/cập nhật thẻ: {uid} - {name}'})
            socketio.emit('cards_updated', self.get_authorized_cards())
            return True, "Thêm thẻ thành công"
        except Exception as e:
            return False, f"Không thể thêm thẻ: {str(e)}"

    def edit_card_name(self, uid, new_name):
        uid = uid.strip().upper()
        new_name = new_name.strip()
        if not uid or not new_name:
            return False, "UID hoặc tên mới không hợp lệ."
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE authorized_cards SET name = ? WHERE uid = ?", (new_name, uid))
                conn.commit()
            socketio.emit('log_message', {'message': f'✓ Đã cập nhật tên thẻ {uid} thành "{new_name}"'})
            socketio.emit('cards_updated', self.get_authorized_cards())
            return True, "Cập nhật tên thẻ thành công"
        except Exception as e:
            return False, f"Lỗi khi cập nhật tên thẻ: {str(e)}"

    def remove_card(self, uid):
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM authorized_cards WHERE uid = ?', (uid,))
                conn.commit()
            self.authorized_cards.discard(uid)
            socketio.emit('log_message', {'message': f'✓ Đã xóa thẻ: {uid}'})
            socketio.emit('cards_updated', self.get_authorized_cards())
            return True, "Xóa thẻ thành công"
        except Exception as e:
            return False, f"Không thể xóa thẻ: {str(e)}"
    
    def get_authorized_cards(self):
        with self.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT uid, name, created_at FROM authorized_cards ORDER BY created_at DESC')
            return [{'uid': row[0], 'name': row[1], 'created_at': row[2]} for row in cursor.fetchall()]
    
    def get_recent_logs(self, limit=50):
        with self.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT timestamp, direction, card_uid, status FROM access_log ORDER BY id DESC LIMIT ?',(limit,))
            return [{'timestamp': row[0], 'direction': row[1], 'card_uid': row[2], 'status': row[3]} for row in cursor.fetchall()]
    
    def toggle_auto_add_mode(self):
        self.auto_add_mode = not self.auto_add_mode
        status_text = "Bật" if self.auto_add_mode else "Tắt"
        socketio.emit('log_message', {'message': f'Chế độ tự động thêm thẻ đã {status_text}.'})
        return self.auto_add_mode

# Initialize RFID system
rfid_system = RFIDControlSystem()

# --- Flask Routes (Không thay đổi) ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/connect', methods=['POST'])
def connect():
    data = request.json
    success, message = rfid_system.connect_arduino(data.get('com_port'), int(data.get('baud_rate')))
    return jsonify({'success': success, 'message': message})

@app.route('/api/disconnect', methods=['POST'])
def disconnect():
    rfid_system.disconnect_arduino()
    return jsonify({'success': True})

@app.route('/api/add_card', methods=['POST'])
def add_card():
    data = request.json
    success, message = rfid_system.add_card(data.get('uid'), data.get('name'))
    return jsonify({'success': success, 'message': message})

@app.route('/api/edit_card', methods=['POST'])
def edit_card():
    data = request.json
    success, message = rfid_system.edit_card_name(data.get('uid'), data.get('name'))
    return jsonify({'success': success, 'message': message})

@app.route('/api/remove_card', methods=['POST'])
def remove_card():
    data = request.json
    success, message = rfid_system.remove_card(data.get('uid'))
    return jsonify({'success': success, 'message': message})

@app.route('/api/toggle_auto_add', methods=['POST'])
def toggle_auto_add():
    mode = rfid_system.toggle_auto_add_mode()
    return jsonify({'auto_add_mode': mode})

@app.route('/api/cards')
def get_cards():
    return jsonify(rfid_system.get_authorized_cards())

@app.route('/api/logs')
def get_logs():
    return jsonify(rfid_system.get_recent_logs())

@app.route('/api/status')
def get_status():
    return jsonify({
        'connected': rfid_system.is_connected,
        'com_port': rfid_system.current_com_port,
        'baud_rate': rfid_system.current_baud_rate,
        'auto_add_mode': rfid_system.auto_add_mode
    })

# =======================================================
# ====> WEBRTC SIGNALING HANDLERS <====
# =======================================================

async def cleanup_connection(connection_id):
    """Dọn dẹp kết nối WebRTC và camera reader"""
    try:
        if connection_id in camera_readers:
            reader = camera_readers.pop(connection_id)
            reader.stop()
            logger.info(f"Đã dừng CameraReader: {connection_id}")
            
        if connection_id in peer_connections:
            pc = peer_connections.pop(connection_id)
            await pc.close()
            logger.info(f"Đã đóng PeerConnection: {connection_id}")
            
    except Exception as e:
        logger.error(f"Lỗi khi dọn dẹp connection {connection_id}: {e}")

@socketio.on('offer')
def handle_offer(data):
    """Xử lý WebRTC offer từ client"""
    sid = request.sid
    direction = data.get('direction')
    url = data.get('url')
    connection_id = f"{sid}_{direction}"
    
    if not all([direction, url, connection_id]):
        logger.error(f"Offer không hợp lệ từ {sid}: {data}")
        return

    logger.info(f"Nhận offer từ {sid} cho {direction}, URL: {url}")
    
    def run_async_task():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(handle_offer_async(connection_id, data, sid, direction, url))
        finally:
            loop.close()
    
    threading.Thread(target=run_async_task, daemon=True).start()

async def handle_offer_async(connection_id, data, sid, direction, url):
    """Async handler cho WebRTC offer"""
    try:
        # Dọn dẹp kết nối cũ trước khi tạo mới
        await cleanup_connection(connection_id)
        
        loop = asyncio.get_running_loop()
        camera_ready_event = asyncio.Event()
        
        # Tạo và khởi động camera reader
        camera_reader = CameraReader(url, direction, loop, camera_ready_event)
        camera_readers[connection_id] = camera_reader
        camera_reader.start()

        # Đợi camera sẵn sàng hoặc timeout sau 10 giây
        logger.info(f"[{connection_id}] Đang đợi camera sẵn sàng...")
        try:
            await asyncio.wait_for(camera_ready_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error(f"[{connection_id}] Camera không sẵn sàng sau 10 giây.")
            await cleanup_connection(connection_id)
            return

        logger.info(f"[{connection_id}] Camera đã sẵn sàng. Bắt đầu thiết lập WebRTC.")
        
        # Tạo peer connection
        pc = RTCPeerConnection()
        peer_connections[connection_id] = pc
        
        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info(f"[{connection_id}] Trạng thái kết nối: {pc.connectionState}")
            if pc.connectionState in ["failed", "closed", "disconnected"]:
                await cleanup_connection(connection_id)
        
        # Thêm video track
        video_track = OpenCVVideoStreamTrack(camera_reader)
        pc.addTrack(video_track)
        
        # Xử lý offer và tạo answer
        offer = RTCSessionDescription(sdp=data['sdp'], type=data['type'])
        await pc.setRemoteDescription(offer)
        
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        
        # Gửi answer về client
        socketio.emit('answer', {
            'sdp': pc.localDescription.sdp,
            'type': pc.localDescription.type,
            'direction': direction
        }, room=sid)
        
        logger.info(f"Đã gửi answer cho {connection_id}")
        
    except Exception as e:
        logger.error(f"Lỗi trong handle_offer_async cho {connection_id}: {e}", exc_info=True)
        socketio.emit('error', {'message': f'Lỗi thiết lập WebRTC: {str(e)}'}, room=sid)
        await cleanup_connection(connection_id)


@socketio.on('disconnect')
def handle_disconnect():
    """Xử lý khi client ngắt kết nối"""
    sid = request.sid
    logger.info(f"Client ngắt kết nối: {sid}")
    
    def cleanup_all():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(cleanup_connection(f"{sid}_in"))
            loop.run_until_complete(cleanup_connection(f"{sid}_out"))
        finally:
            loop.close()
    
    threading.Thread(target=cleanup_all, daemon=True).start()

if __name__ == '__main__':
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    # Copy index.html to templates if it exists in the root
    if os.path.exists('index.html') and not os.path.exists('templates/index.html'):
        import shutil
        shutil.copy('index.html', 'templates/index.html')

    logger.info("Khởi động server...")
    socketio.run(app, debug=False, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)