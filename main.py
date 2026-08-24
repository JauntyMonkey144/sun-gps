from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, send_from_directory
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo # <<< THÊM DÒNG NÀY
from flask_cors import CORS
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from flask import get_flashed_messages
import os, requests, uuid, json # Đã thêm lại json
from functools import wraps
import logging
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter # <<< THÊM DÒNG NÀY
# ---- (THÊM MỚI) Thư viện cho FCM và Lập lịch ----
import firebase_admin
from firebase_admin import credentials, messaging
from apscheduler.schedulers.background import BackgroundScheduler

# ---- Configure Logging ----
logging.basicConfig(level=logging.INFO) # Đổi sang INFO
logger = logging.getLogger(__name__)

# ---- Init App ----
app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ['SECRET_KEY']
CORS(app)

# ---- Cấu hình Domain chính cho ứng dụng ----
SERVER_NAME = os.environ.get("SERVER_NAME")
app.config["SERVER_NAME"] = SERVER_NAME
app.config["PREFERRED_URL_SCHEME"] = "https"
# (THÊM MỚI) Dùng cho link trong thông báo đẩy
BASE_URL = f"https://{SERVER_NAME}" 

# ---- Timezone VN ----
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh") # <<< SỬA DÒNG NÀY

load_dotenv() # Tải các biến từ file .env

# ---- MongoDB config ----
# ---- Khoá HERE Maps ----
# Dùng cho REST geocoding VÀ proxy tile bản đồ, đều ở phía server. Bản đồ trong trình duyệt dùng Leaflet +
# OpenStreetMap nên không cần khoá — khoá này không bao giờ rời khỏi server.
HERE_API_KEY = os.environ['HERE_API_KEY']
# Kiểu bản đồ HERE: explore.day | explore.night | lite.day | logistics.day
HERE_TILE_STYLE = os.environ.get('HERE_TILE_STYLE', 'explore.day')

# ---- Firebase Web (công khai theo thiết kế — bảo mật nằm ở Security Rules) ----
FIREBASE_WEB_CONFIG = os.environ.get('FIREBASE_WEB_CONFIG_JSON', '{}')
FCM_VAPID_KEY = os.environ.get('FCM_VAPID_KEY', '')

MONGO_URI = os.environ['MONGO_URI']
DB_NAME = os.environ['DB_NAME']
client = MongoClient(MONGO_URI)
db = client[DB_NAME]

# ---- (THÊM MỚI) Cấu hình HTTP Client với Connection Pool lớn hơn ----
# Tạo một session tùy chỉnh
firebase_session = requests.Session()

# Tạo một adapter với pool size lớn hơn (ví dụ: 50, an toàn cho 13+ kết nối)
adapter = HTTPAdapter(pool_maxsize=50) 

# Gắn adapter này cho tất cả các request https
firebase_session.mount('https://', adapter)
# ---- HẾT PHẦN THÊM MỚI ----

# ---- (THÊM MỚI) Khởi tạo Firebase Admin SDK ----
try:
    service_account_json_str = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON')
    if service_account_json_str:
        service_account_info = json.loads(service_account_json_str)
        cred = credentials.Certificate(service_account_info)
        
        # (CẬP NHẬT) Truyền http_client tùy chỉnh vào
        firebase_admin.initialize_app(cred, {
            'http_client': firebase_session
        })
        
        logger.info("✅ Firebase Admin SDK đã được khởi tạo (với pool size 50).")
    else:
        logger.error("❌ Biến môi trường FIREBASE_SERVICE_ACCOUNT_JSON chưa được đặt.")
except Exception as e:
    logger.error(f"❌ Lỗi khởi tạo Firebase Admin SDK: {e}", exc_info=True)

# ---- Resend API Config (GIỮ NGUYÊN CỦA BẠN) ----
RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
RESEND_FROM_EMAIL = os.environ.get('RESEND_FROM_EMAIL')


# ---- Người duyệt: nạp từ biến môi trường APPROVERS_JSON (không hard-code email) ----
# Định dạng: {"nghia":{"email":"...","name":"..."}, "thu":{...}, "nhat":{...}, "than":{...}}
try:
    _approvers = json.loads(os.environ["APPROVERS_JSON"])
except KeyError:
    raise RuntimeError("Thiếu biến môi trường APPROVERS_JSON")
except json.JSONDecodeError as e:
    raise RuntimeError(f"APPROVERS_JSON không phải JSON hợp lệ: {e}")

APPROVER_EMAILS = {k: v["email"] for k, v in _approvers.items()}
APPROVER_NAMES  = {v["email"]: v["name"] for v in _approvers.values()}

# ---- Quy trình duyệt theo phòng ban (sử dụng Position) ----
APPROVAL_WORKFLOWS = {
    "Phòng Kỹ Thuật": [APPROVER_EMAILS["nghia"]],
    "Phòng Sales Admin": [APPROVER_EMAILS["thu"]],
    "Phòng Kinh Doanh": [APPROVER_EMAILS["nhat"]]
}

# ---- Danh sách CC cuối cùng sau khi duyệt thành công (GIỮ NGUYÊN) ----
FINAL_CC_LISTS = {
    "Phòng Kỹ Thuật": [APPROVER_EMAILS["nghia"], APPROVER_EMAILS["thu"], APPROVER_EMAILS["than"]],
    "Phòng Sales Admin": [APPROVER_EMAILS["nghia"], APPROVER_EMAILS["thu"], APPROVER_EMAILS["than"]],
    "Phòng Kinh Doanh": [APPROVER_EMAILS["nhat"], APPROVER_EMAILS["nghia"], APPROVER_EMAILS["thu"], APPROVER_EMAILS["than"]]
}

# ---- Thời gian hết hạn token duyệt (7 ngày) (GIỮ NGUYÊN) ----
APPROVAL_EXPIRY_DAYS = 7

# ---- Cấu hình thời gian nhắc nhở (5 phút để test) (GIỮ NGUYÊN) ----
REMINDER_INTERVAL_MINUTES = 5

# ---- (GIỮ NGUYÊN) Hàm gửi email bằng Resend API ----
def send_email(to_email, subject, body, cc=None):
    """Gửi email bằng Resend API."""
    try:
        if not RESEND_API_KEY:
            logger.error("❌ RESEND_API_KEY environment variable not set.")
            return False

        # Xử lý người nhận TO
        to_list = []
        if isinstance(to_email, list):
            to_list = list(set(filter(None, to_email)))
        elif to_email:
            to_list = [to_email]

        if not to_list:
            logger.warning("⚠️ Email sending skipped: No valid TO recipients.")
            return False

        # Xử lý người nhận CC, đảm bảo không trùng với TO
        cc_list = []
        if cc:
            initial_cc = list(set(filter(None, cc)))
            to_lower = {e.lower() for e in to_list}
            cc_list = [email for email in initial_cc if email.lower() not in to_lower]

        styled_body = f"""
        <div style="font-family: 'Helvetica Neue', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #ffffff; border-radius: 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
            <div style="background-color: #007bff; padding: 15px; border-radius: 8px 8px 0 0; text-align: center;">
                <h1 style="color: #ffffff; margin: 0; font-size: 24px;">Sun Automation System</h1>
            </div>
            <div style="padding: 20px;">
                {body}
            </div>
            <div style="text-align: center; padding: 15px; background-color: #f8f9fa; border-radius: 0 0 8px 8px;">
                <p style="color: #666; font-size: 12px; margin: 0;">Sun Automation System &copy; {datetime.now().year}</p>
            </div>
        </div>
        """

        payload = {
            "from": f"Sun Automation System <{RESEND_FROM_EMAIL}>",
            "to": to_list,
            "subject": subject,
            "html": styled_body
        }
        if cc_list:
            payload["cc"] = cc_list

        headers = {
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json"
        }

        response = requests.post("https://api.resend.com/emails", json=payload, headers=headers)

        if response.status_code == 200:
            all_recipients = to_list + (cc_list if cc_list else [])
            logger.info(f"✅ Email sent successfully via Resend to {all_recipients} with subject: {subject}")
            return True
        else:
            logger.error(f"❌ Resend API error ({response.status_code}): {response.text}")
            return False

    except Exception as e:
        logger.error(f"❌ Email sending failed with exception: {str(e)}")
        return False

# ---- Hàm gửi thông báo duyệt (GIỮ NGUYÊN) ----
def send_approval_notification(record, is_reminder=False):
    """Gửi email yêu cầu duyệt đến người duyệt hiện tại."""
    try:
        workflow = record.get("ApprovalWorkflow", [])
        current_idx = record.get("CurrentApproverIndex", 0)
        if current_idx >= len(workflow):
            logger.warning(f"⚠️ Invalid approver index for record {record.get('_id')}")
            return
        current_approver_email = workflow[current_idx]
        emp_name = record.get("EmployeeName", "N/A")
        emp_id = record.get("EmployeeId", "N/A")
        department = record.get("Department", "N/A")
        
        subject_prefix = "[NHẮC NHỞ] " if is_reminder else ""
        subject = f"{subject_prefix}Yêu cầu duyệt nghỉ phép - {emp_name}"
        
        detail_url = url_for('approve_leave', token=record['ApprovalToken'], _external=True)
        leave_note_display = record.get("LeaveNote") or "Không có ghi chú"
        body = f"""
            <h3 style="color: #333; font-size: 20px; margin-bottom: 15px;">Yêu cầu nghỉ phép cần bạn duyệt</h3>
            <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Nhân viên:</strong> {emp_name} ({emp_id})</p>
            <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Phòng ban:</strong> {department}</p>
            <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Ngày nghỉ:</strong> {record['DisplayDate']}</p>
            <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Lý do:</strong> {record['Reason']}</p>
            <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Ghi chú của nhân viên:</strong> {leave_note_display}</p>
        """
        # Thêm thông tin người duyệt trước nếu đây là bước thứ 2 trở đi
        if current_idx > 0:
            history = record.get("ApprovalHistory", [])
            if history:
                last_approval = history[-1]
                prev_approver_name = last_approval.get("approver", "N/A")
                prev_approval_time = last_approval.get("timestamp", "N/A")
                note_parts = last_approval.get("action", "").split(", ", 1)
                prev_approver_note = note_parts[1] if len(note_parts) > 1 else "Không có ghi chú"
                
                body += f"""
                    <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Đã duyệt bởi:</strong> {prev_approver_name} lúc {prev_approval_time}</p>
                    <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Ghi chú của người duyệt trước:</strong> {prev_approver_note}</p>
                """
        body += f"""
            <p style="margin: 25px 0;">
                <a href="{detail_url}" style="display: inline-block; padding: 12px 24px; background-color: #007bff; color: #ffffff; text-decoration: none; border-radius: 5px; font-size: 16px; font-weight: bold;">Xử lý yêu cầu</a>
            </p>
            <p style="color: #555; line-height: 1.6; font-size: 16px;">Liên kết này sẽ hết hạn sau {APPROVAL_EXPIRY_DAYS} ngày.</p>
        """
        
        # Chỉ gửi email đến current_approver_email, không CC nếu là nhắc nhở
        if is_reminder:
            send_email(current_approver_email, subject, body, cc=None)
        else:
            # Gửi kèm CC cho nhân viên và người duyệt trước (nếu có) khi không phải nhắc nhở
            employee = db.idx_collection.find_one({"EmployeeId": emp_id})
            emp_email = employee.get("Email") if employee else None
            cc_list = [emp_email]
            if current_idx > 0:
                cc_list.append(workflow[current_idx - 1])
            send_email(current_approver_email, subject, body, cc=cc_list)
    except Exception as e:
        logger.error(f"❌ Failed to send approval notification for record {record.get('_id')}: {e}")
            
# ---- (THAY THẾ TOÀN BỘ HÀM NÀY) ----
def send_fcm_push(tokens, title, body, link):
    """
    Gửi push notification (TUẦN TỰ) đến một danh sách token.
    Cách này chậm hơn nhưng tránh được lỗi "Connection pool is full".
    """
    if not tokens:
        logger.info("Không có token nào để gửi FCM.")
        return

    unique_tokens = list(set(tokens))
    success_count = 0
    failure_count = 0
    failed_tokens = []

    logger.info(f"Đang gửi {len(unique_tokens)} thông báo (theo kiểu tuần tự)...")

    # ---- BẮT ĐẦU LOGIC MỚI ----
    try:
        # Loop (vòng lặp) qua từng token và gửi từng cái một
        for token in unique_tokens:
            message = messaging.Message(
                notification=messaging.Notification(
                    title=title,
                    body=body,
                ),
                webpush=messaging.WebpushConfig(
                    fcm_options=messaging.WebpushFCMOptions(link=link)
                ),
                token=token, # Chỉ gửi đến 1 token
            )

            try:
                # Dùng hàm messaging.send() (gửi 1 tin)
                messaging.send(message)
                success_count += 1
            except Exception as e: # <<< SỬA 1: BẮT LỖI CHUNG
                failure_count += 1
                
                # SỬA 2: Kiểm tra xem lỗi có mã 'code' không
                if hasattr(e, 'code'): 
                    logger.warning(f"⚠️ Gửi FCM thất bại cho token {token[:10]}...: {e.code}")
                    # Kiểm tra mã lỗi cụ thể
                    if e.code == 'messaging/registration-token-not-registered':
                        failed_tokens.append(token)
                else:
                    # Nếu là lỗi khác (không có .code)
                    logger.error(f"❌ Lỗi không xác định khi gửi cho token {token[:10]}...: {e}")

        # ---- KẾT THÚC LOGIC MỚI ----

        # Báo cáo kết quả
        logger.info(f"✅ Gửi FCM hoàn tất. Thành công: {success_count} tin.")
        if failure_count > 0:
            logger.warning(f"⚠️ Gửi FCM thất bại: {failure_count} tin.")
        
        # Xóa các token hỏng (giữ nguyên logic)
        if failed_tokens:
            logger.info(f"Đang xóa {len(failed_tokens)} token đã hết hạn...")
            db.fcm_tokens.update_many(
                {},
                {"$pull": {"tokens": {"$in": failed_tokens}}}
            )
            
    except Exception as e:
        # Bắt các lỗi nghiêm trọng (ví dụ: xác thực Firebase thất bại)
        logger.error(f"❌ Lỗi nghiêm trọng khi đang gửi FCM: {e}", exc_info=True)

# ---- (THÊM MỚI) Hàm lập lịch nhắc nhở Check-in (8h sáng) ----
def schedule_checkin_reminder():
    """Gửi nhắc nhở check-in đến TẤT CẢ nhân viên."""
    with app.app_context():
        logger.info("⏰ [Scheduler] Đang chạy tác vụ nhắc nhở Check-in 8h...")
        all_token_docs = db.fcm_tokens.find({}, {"tokens": 1})
        all_tokens = []
        for doc in all_token_docs:
            all_tokens.extend(doc.get("tokens", []))
        
        if all_tokens:
            send_fcm_push(
                tokens=all_tokens,
                title="Nhắc nhở Chấm công ⏰",
                body="Đừng quên check-in nhé!",
                link=BASE_URL
            )
        else:
            logger.info("[Scheduler] Không tìm thấy token nào để nhắc check-in.")

# ---- (THÊM MỚI) Hàm lập lịch nhắc nhở Check-out (17h chiều) ----
def schedule_checkout_reminder():
    """Gửi nhắc nhở check-out CHỈ cho những người đã check-in nhưng CHƯA check-out."""
    with app.app_context():
        logger.info("⏰ [Scheduler] Đang chạy tác vụ nhắc nhở Check-out 17h...")
        now = datetime.now(VN_TZ)
        today_str = now.strftime("%d/%m/%Y")
        
        try:
            checked_in_users = db.alt_checkins.distinct("EmployeeId", {
                "CheckinDate": today_str,
                "CheckType": "checkin"
            })
            
            if not checked_in_users:
                logger.info("[Scheduler] Không có ai check-in hôm nay.")
                return

            checked_out_users = db.alt_checkins.distinct("EmployeeId", {
                "CheckinDate": today_str,
                "CheckType": "checkout"
            })
            
            users_to_remind = list(set(checked_in_users) - set(checked_out_users))
            
            if not users_to_remind:
                logger.info("[Scheduler] Tất cả đã check-out, không cần nhắc nhở.")
                return

            token_docs = db.fcm_tokens.find({"EmployeeId": {"$in": users_to_remind}})
            tokens_to_send = []
            for doc in token_docs:
                tokens_to_send.extend(doc.get("tokens", []))

            if tokens_to_send:
                logger.info(f"[Scheduler] Gửi {len(tokens_to_send)} thông báo nhắc check-out.")
                send_fcm_push(
                    tokens=tokens_to_send,
                    title="Nhắc nhở Chấm công ⏰",
                    body="Đừng quên check-out nhé!",
                    link=BASE_URL
                )
            else:
                logger.info("[Scheduler] Không tìm thấy token cho những người cần nhắc check-out.")
        
        except Exception as e:
            logger.error(f"❌ Lỗi trong schedule_checkout_reminder: {e}", exc_info=True)

# ---- (THÊM MỚI) Hàm lập lịch nhắc nhở đơn nghỉ phép quá hạn ----
def schedule_overdue_leave_reminder():
    """
    Gửi nhắc nhở cho các đơn nghỉ phép bị "Chờ duyệt" quá 2 ngày.
    Chạy 1 lần/ngày lúc 9h sáng.
    """
    with app.app_context():
        logger.info("⏰ [Scheduler] Đang chạy tác vụ nhắc nhở đơn nghỉ phép quá hạn...")
        
        # 1. Đặt mốc thời gian là 1 ngày trước
        today_start = datetime.now(VN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)        
        # 2. Tìm các đơn "Chờ duyệt" VÀ được tạo từ 1 ngày trước trở về cũ
        query = {
            "Status": "Chờ duyệt",
            "CreationTime": {"$lt": today_start} # $lte = less than or equal
        }
        
        overdue_requests = list(db.alt_checkins.find(query))
        
        if not overdue_requests:
            logger.info("[Scheduler] Không có đơn nghỉ phép nào quá hạn.")
            return

        logger.info(f"[Scheduler] Tìm thấy {len(overdue_requests)} đơn quá hạn. Đang gửi nhắc nhở...")
        
        for record in overdue_requests:
            try:
                emp_name = record.get("EmployeeName", "N/A")
                logger.info(f"Gửi nhắc nhở cho đơn của: {emp_name}...")
                
                # 3. Tái sử dụng hàm gửi mail cũ, với cờ is_reminder=True
                # Hàm này đã biết ai là người duyệt tiếp theo (CurrentApproverIndex)
                send_approval_notification(record, is_reminder=True)
                
            except Exception as e:
                logger.error(f"❌ Lỗi khi gửi nhắc nhở quá hạn cho đơn {record.get('_id')}: {e}", exc_info=True)

        logger.info(f"[Scheduler] Hoàn tất gửi {len(overdue_requests)} nhắc nhở quá hạn.")

# ---- Hàm lấy địa chỉ từ lat/lng (CẬP NHẬT SANG HERE MAPS) ----
def get_address_from_latlng(lat, lng):
    if not lat or not lng: return ""
    
    here_api_key = HERE_API_KEY
    
    url = f"https://revgeocode.search.hereapi.com/v1/revgeocode?at={lat},{lng}&lang=vi-VN&apikey={here_api_key}"
    
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("items") and len(data["items"]) > 0:
                # Trả về nhãn địa chỉ đầy đủ từ HERE Maps
                return data["items"][0]["address"]["label"]
    except Exception as e:
        logger.error(f"⚠️ Reverse geocode error (HERE Maps): {str(e)}")
        
    return f"({lat}, {lng})"
# ---- Decorator login required (GIỮ NGUYÊN) ----
def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if 'email' not in session:
            next_url = request.path
            return redirect(url_for('login', next=next_url))
        return f(*args, **kwargs)
    return wrap

# ---- Helper render flash messages (GIỮ NGUYÊN) ----
def render_flash_messages():
    messages_html = ""
    for category, message in get_flashed_messages(with_categories=True):
        color = "green" if category == "success" else "red"
        messages_html += f'<p style="color: {color}; margin: 10px 0; padding: 5px; background: #f8f9fa; border-radius: 4px;">{message}</p>'
    return messages_html

# ---- Hàm lấy tên người dùng từ email (GIỮ NGUYÊN) ----
def get_username_from_email(email):
    if not email:
        return "Unknown"
    user = db.admins.find_one({"email": email.lower()}) or db.users.find_one({"email": email.lower()})
    return user.get("username", APPROVER_NAMES.get(email.lower(), email.split("@")[0])) if user else APPROVER_NAMES.get(email.lower(), email.split("@")[0])

# ---- Trang đăng nhập (GIỮ NGUYÊN) ----
@app.route("/login", methods=["GET", "POST"])
def login():
    if "email" in session:
        return redirect(url_for("leave_requests"))
    next_url = request.args.get("next", "")
    if request.method == "POST":
        email = request.form.get("email", "").lower()
        password = request.form.get("password")
        form_next = request.form.get("next", "")
        next_url = form_next or next_url
        admin = db.admins.find_one({"email": email}) or db.users.find_one({"email": email})
        if admin and check_password_hash(admin["password"], password):
            session["email"] = email
            session["username"] = admin.get("username", email.split("@")[0])
            session["admin_id"] = str(admin["_id"])
            flash(f"Đăng nhập thành công! Chào {session['username']}", "success")
            if next_url and next_url.startswith('/'):
                return redirect(next_url)
            return redirect(url_for("leave_requests"))
        else:
            flash("Sai tài khoản hoặc mật khẩu", "danger")
    return f"""
    <!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Đăng nhập Admin</title><style>
    body {{ font-family: Arial, sans-serif; background: #f4f6f9; margin: 0; padding: 20px; }}
    .container {{ max-width: 400px; margin: 100px auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
    input {{ width: 100%; padding: 10px; margin: 10px 0; box-sizing: border-box; border: 1px solid #ddd; border-radius: 4px; }}
    button {{ background: #007bff; color: white; padding: 12px; width: 100%; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }}
    button:hover {{ background: #0056b3; }}
    a {{ color: #007bff; text-decoration: none; display: block; margin-top: 10px; text-align: center; }}
    h2 {{ text-align: center; color: #333; }}
    </style></head><body><div class="container">
    <h2>🔐 Đăng nhập Admin</h2>{render_flash_messages()}
    <form method="POST"><input type="hidden" name="next" value="{next_url}">
    <input type="email" name="email" placeholder="Email đăng nhập" required>
    <input type="password" name="password" placeholder="Mật khẩu" required>
    <button type="submit">Đăng nhập</button></form>
    <a href="/forgot-password">Quên mật khẩu?</a></div></body></html>"""

# ---- Trang danh sách đơn xin nghỉ (GIỮ NGUYÊN) ----
@app.route("/leave-requests")
@login_required
def leave_requests():
    pending_requests = list(db.alt_checkins.find({"Status": "Chờ duyệt"}).sort("CreationTime", -1))
    rows_html = ""
    for req in pending_requests:
        emp = req.get("EmployeeName", "N/A")
        emp_id = req.get("EmployeeId", "")
        department = req.get("Department", "N/A")
        create_time_obj = req.get("CreationTime")
        if isinstance(create_time_obj, datetime):
            if create_time_obj.tzinfo is None:
                create_time_obj = create_time_obj.replace(tzinfo=timezone.utc)
            create_time_vn = create_time_obj.astimezone(VN_TZ)
            create_time_str = create_time_vn.strftime("%d/%m/%Y %H:%M:%S")
        else:
            create_time_str = "N/A"
        leave_date = req.get("DisplayDate", "")
        reason = req.get("Reason", "")
        token = req.get("ApprovalToken", "")
        status = req.get("Status", "N/A")
        current_approver_idx = req.get("CurrentApproverIndex", 0)
        current_approver_email = req.get("ApprovalWorkflow", [])[current_approver_idx] if req.get("ApprovalWorkflow") else "N/A"
        current_approver_name = get_username_from_email(current_approver_email)
        rows_html += f"""
        <tr><td>{emp}</td><td>{emp_id}</td><td>{department}</td><td>{create_time_str}</td>
            <td>{leave_date}</td><td>{reason}</td><td>{status} (Duyệt bởi: {current_approver_name})</td>
            <td><a href="/approve/{token}" class="btn-detail">Chi tiết</a></td>
        </tr>"""
    return f"""
    <!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8"><title>Danh sách đơn xin nghỉ</title><style>
    body {{ font-family: Arial, sans-serif; background: #f4f6f9; margin: 0; padding: 20px; }}
    .container {{ max-width: 1400px; margin: 40px auto; background: white; border-radius: 10px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); padding: 30px; }}
    .header-bar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
    .logout-btn {{ background: #dc3545; color: white; border: none; padding: 8px 16px; text-decoration: none; border-radius: 6px; margin-left: 15px;}}
    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
    th, td {{ padding: 10px; border: 1px solid #dee2e6; text-align: left; }}
    th {{ background: #f8f9fa; }}
    .btn-detail {{ display: block; background: #007bff; color: white; padding: 6px 12px; border-radius: 4px; text-decoration: none; text-align: center; white-space: nowrap; }}
    </style></head><body><div class="container">
        <div class="header-bar">
            <h2>Danh sách đơn xin nghỉ chờ duyệt</h2>
            <div><span>Xin chào, {session.get('username')}</span>
                    <a href="{url_for('logout')}" class="logout-btn">Đăng xuất</a>
            </div>
        </div>
        <table>
            <tr><th>Nhân viên</th><th>ID</th><th>Phòng ban</th><th>Ngày tạo</th><th>Ngày nghỉ</th><th>Lý do</th><th>Trạng thái</th><th>Thao tác</th></tr>
            {rows_html or '<tr><td colspan="8" style="text-align:center;">Không có đơn chờ duyệt</td></tr>'}
        </table>
    </div></body></html>"""

# ---- Trang quên mật khẩu (GIỮ NGUYÊN) ----
@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").lower()
        user = db.admins.find_one({"email": email}) or db.users.find_one({"email": email})
        if user:
            reset_token = str(uuid.uuid4())
            collection_name = "admins" if db.admins.count_documents({"_id": user["_id"]}) > 0 else "users"
            db[collection_name].update_one(
                {"_id": user["_id"]},
                {"$set": {"reset_token": reset_token, "reset_expiry": datetime.now(VN_TZ) + timedelta(hours=1)}}
            )
            reset_url = url_for('reset_password', token=reset_token, _external=True)
            username = user.get("username", "bạn")

            body = f"""
                <p>Chào {username},</p>
                <p>Chúng tôi đã nhận được yêu cầu đặt lại mật khẩu cho tài khoản của bạn.</p>
                <p>Vui lòng nhấp vào liên kết dưới đây để tạo một mật khẩu mới:</p>
                <a href="{reset_url}" style="display: inline-block; padding: 12px 24px; background-color: #007bff; color: #ffffff; text-decoration: none; border-radius: 5px; font-size: 16px; font-weight: bold;">Reset</a>
                <p>Liên kết này sẽ chỉ có hiệu lực trong vòng 1 giờ.</p>
                <p>Nếu bạn không phải là người yêu cầu, vui lòng bỏ qua email này.</p>
                <p>Trân trọng,<br>Đội ngũ Sun Automation System</p>
            """

            if send_email(email, subject=f"Yêu cầu đặt lại mật khẩu - Sun Automation", body=body):
                flash("Email đặt lại mật khẩu đã được gửi! Vui lòng kiểm tra hộp thư của bạn.", "success")
            else:
                flash("Lỗi gửi email đặt lại mật khẩu. Vui lòng thử lại sau!", "danger")
        else:
            flash("Email không tồn tại trong hệ thống!", "danger")
        return redirect(url_for("login"))

    return f"""
    <!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Quên mật khẩu</title><style>
    body {{ font-family: Arial, sans-serif; background: #f4f6f9; margin: 0; padding: 20px; }}
    .container {{ max-width: 400px; margin: 100px auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
    input {{ width: 100%; padding: 10px; margin: 10px 0; box-sizing: border-box; border: 1px solid #ddd; border-radius: 4px; }}
    button {{ background: #007bff; color: white; padding: 12px; width: 100%; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }}
    button:hover {{ background: #0056b3; }}
    a {{ color: #007bff; text-decoration: none; display: block; margin-top: 10px; text-align: center; }}
    h2 {{ text-align: center; color: #333; }}
    </style></head><body><div class="container">
    <h2>🔑 Quên mật khẩu</h2>{render_flash_messages()}
    <form method="POST">
    <input type="email" name="email" placeholder="Nhập email của bạn" required>
    <button type="submit">Gửi yêu cầu đặt lại</button></form>
    <a href="/login">Quay lại đăng nhập</a></div></body></html>
    """

# ---- Trang đặt lại mật khẩu (GIỮ NGUYÊN) ----
@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    admin = db.admins.find_one({"reset_token": token, "reset_expiry": {"$gt": datetime.now(VN_TZ)}}) or \
            db.users.find_one({"reset_token": token, "reset_expiry": {"$gt": datetime.now(VN_TZ)}})
    if not admin:
        flash("Token không hợp lệ hoặc đã hết hạn!", "danger")
        return redirect(url_for("login"))
    if request.method == "POST":
        new_password = request.form.get("password")
        collection = "admins" if db.admins.count_documents({"_id": admin["_id"]}) > 0 else "users"
        db[collection].update_one(
            {"_id": admin["_id"]},
            {"$set": {"password": generate_password_hash(new_password), "reset_token": None, "reset_expiry": None}}
        )
        flash("Mật khẩu đã được cập nhật thành công!", "success")
        return redirect(url_for("login"))
    return f"""
    <!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Đặt lại mật khẩu</title><style>
    body {{ font-family: Arial, sans-serif; background: #f4f6f9; margin: 0; padding: 20px; }}
    .container {{ max-width: 400px; margin: 100px auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
    input {{ width: 100%; padding: 10px; margin: 10px 0; box-sizing: border-box; border: 1px solid #ddd; border-radius: 4px; }}
    button {{ background: #007bff; color: white; padding: 12px; width: 100%; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }}
    button:hover {{ background: #0056b3; }}
    a {{ color: #007bff; text-decoration: none; display: block; margin-top: 10px; text-align: center; }}
    h2 {{ text-align: center; color: #333; }}
    </style></head><body><div class="container">
    <h2>🔑 Đặt lại mật khẩu</h2>{render_flash_messages()}
    <form method="POST">
    <input type="password" name="password" placeholder="Nhập mật khẩu mới" required>
    <button type="submit">Cập nhật mật khẩu</button></form>
    <a href="/login">Quay lại đăng nhập</a></div></body></html>
    """

# ---- Logout (GIỮ NGUYÊN) ----
@app.route("/logout")
def logout():
    session.clear()
    flash("Đăng xuất thành công!", "success")
    return redirect(url_for("login"))

# ---- Trang chính (GIỮ NGUYÊN) ----
@app.route("/")
def home():
    return render_template(
        "index.html",
        firebase_config=FIREBASE_WEB_CONFIG,
        vapid_key=FCM_VAPID_KEY
    )


# ---- Proxy geocoding: trình duyệt gọi endpoint này, khoá HERE ở lại server ----
@app.route("/api/geocode")
def api_geocode():
    lat = request.args.get("lat")
    lng = request.args.get("lng")
    if not lat or not lng:
        return jsonify({"error": "Thiếu lat/lng"}), 400
    return jsonify({"address": get_address_from_latlng(lat, lng)})


# ---- Proxy tile bản đồ HERE ----
# Trình duyệt xin ảnh bản đồ từ chính server này, khoá HERE không bao giờ lộ ra ngoài.
# Cache trong RAM để giảm số lần gọi HERE (nhân viên thường chấm công ở vài địa điểm quen).
_TILE_CACHE = {}
_TILE_CACHE_MAX = 2000

@app.route("/api/tiles/<int:z>/<int:x>/<int:y>.png")
def here_tile(z, x, y):
    if not (0 <= z <= 20):
        return ("", 400)

    key = (z, x, y, HERE_TILE_STYLE)
    cached = _TILE_CACHE.get(key)
    if cached is None:
        url = (
            f"https://maps.hereapi.com/v3/base/mc/{z}/{x}/{y}/png8"
            f"?apiKey={HERE_API_KEY}&style={HERE_TILE_STYLE}&size=512&lang=vi"
        )
        try:
            r = requests.get(url, timeout=8)
        except Exception as e:
            logger.error(f"Lỗi tải tile HERE: {e}")
            return ("", 502)

        if r.status_code != 200:
            logger.warning(f"HERE tile {z}/{x}/{y} trả về {r.status_code}")
            return ("", r.status_code if r.status_code in (401, 403, 404) else 502)

        cached = r.content
        if len(_TILE_CACHE) >= _TILE_CACHE_MAX:
            _TILE_CACHE.clear()
        _TILE_CACHE[key] = cached

    resp = app.response_class(cached, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp

# ---- (THÊM MỚI) Route để phục vụ Service Worker từ thư mục 'templates' ----
@app.route('/firebase-messaging-sw.js')
def sw():
    return app.response_class(
        render_template('firebase-messaging-sw.js', firebase_config=FIREBASE_WEB_CONFIG),
        mimetype='application/javascript'
    )

# ---- (THÊM MỚI) API Endpoint để đăng ký FCM token ----
@app.route("/api/register-token", methods=["POST"])
def register_token():
    data = request.get_json()
    emp_id = data.get("EmployeeId")
    token = data.get("token")
    
    if not emp_id or not token:
        logger.warning(f"Đăng ký token thất bại: Thiếu EmployeeId hoặc token.")
        return jsonify({"message": "Thiếu thông tin"}), 400
    
    try:
        # Sử dụng $addToSet để thêm token vào mảng mà không bị trùng lặp
        db.fcm_tokens.update_one(
            {"EmployeeId": emp_id},
            {"$addToSet": {"tokens": token}},
            upsert=True  # Tạo mới document nếu EmployeeId chưa tồn tại
        )
        logger.info(f"✅ Token FCM đã được đăng ký/cập nhật cho EmployeeId: {emp_id}")
        return jsonify({"message": "Token đã được đăng ký"}), 200
    except Exception as e:
        logger.error(f"❌ Lỗi khi đăng ký token cho {emp_id}: {e}", exc_info=True)
        return jsonify({"message": "Lỗi máy chủ khi đăng ký token"}), 500

# ---- API: Check trước khi chấm công (GIỮ NGUYÊN) ----
@app.route("/api/checkin-preview", methods=["POST"])
def checkin_preview():
    data = request.get_json()
    emp_id = data.get("EmployeeId")
    if not emp_id: return jsonify({"message": "❌ Thiếu mã nhân viên"}), 400
    employee = db.idx_collection.find_one({"EmployeeId": emp_id})
    if not employee: return jsonify({"message": "❌ Mã nhân viên không có trong danh sách"}), 404
    today_str = datetime.now(VN_TZ).strftime("%d/%m/%Y")
    checkin_count = db.alt_checkins.count_documents({
        "EmployeeId": emp_id, "CheckinDate": today_str, "CheckType": "checkin"
    })
    checkout_count = db.alt_checkins.count_documents({
        "EmployeeId": emp_id, "CheckinDate": today_str, "CheckType": "checkout"
    })
    return jsonify({
        "EmployeeId": emp_id,
        "EmployeeName": employee.get("EmployeeName"),
        "CheckinCount": checkin_count,
        "CheckoutCount": checkout_count,
        "HasCheckedOut": checkout_count > 0
    })

# ---- API: Checkin (GIỮ NGUYÊN) ----
@app.route("/api/checkin", methods=["POST"])
def checkin():
    data = request.get_json()
    emp_id = data.get("EmployeeId")
    check_type = data.get("CheckType", "checkin")
    employee = db.idx_collection.find_one({"EmployeeId": emp_id})
    if not employee: 
        return jsonify({"message": "❌ Mã nhân viên không có"}), 404

    now = datetime.now(VN_TZ)
    checkin_date_str = now.strftime("%d/%m/%Y")
    creation_time_str = now.strftime("%H:%M:%S")

    if check_type == 'checkin':
        has_checked_out = db.alt_checkins.count_documents({
            "EmployeeId": emp_id,
            "CheckinDate": checkin_date_str,
            "CheckType": "checkout"
        })
        if has_checked_out > 0:
            return jsonify({"message": "🚫 Bạn đã check-out trong ngày, không thể check-in lại!"}), 400

        count = db.alt_checkins.count_documents({
            "EmployeeId": emp_id, 
            "CheckinDate": checkin_date_str, 
            "CheckType": check_type
        })
        if count >= 9:
            return jsonify({"message": "🚫 Đã đạt tối đa 9 lần Check-in hôm nay!"}), 400

    elif check_type == 'checkout':
        has_checked_in = db.alt_checkins.count_documents({
            "EmployeeId": emp_id,
            "CheckinDate": checkin_date_str,
            "CheckType": "checkin"
        })
        if has_checked_in == 0:
            return jsonify({"message": "🚫 Bạn chưa check-in hôm nay, không thể check-out!"}), 400

        count = db.alt_checkins.count_documents({
            "EmployeeId": emp_id,
            "CheckinDate": checkin_date_str,
            "CheckType": "checkout"
        })
        if count >= 1:
            return jsonify({"message": "🚫 Chỉ được Check-out 1 lần mỗi ngày!"}), 400
    else:
        return jsonify({"message": "❌ Loại check không hợp lệ"}), 400

    db.alt_checkins.insert_one({
        "EmployeeId": emp_id, 
        "EmployeeName": employee.get("EmployeeName"),
        "Latitude": data.get("Latitude"), 
        "Longitude": data.get("Longitude"),
        "Address": get_address_from_latlng(data.get("Latitude"), data.get("Longitude")),
        "CreationTime": creation_time_str,
        "CheckinDate": checkin_date_str,
        "Timestamp": now,
        "Status": "Đã xác nhận", 
        "FaceImage": data.get("FaceImage"),
        "ProjectId": data.get("ProjectId"), 
        "Tasks": data.get("Tasks", []),
        "CheckinNote": data.get("CheckinNote", ""), 
        "CheckType": check_type,
    })

    if check_type == 'checkout':
        return jsonify({
            "message": "✅ Check-out thành công!",
            "EmployeeName": employee.get("EmployeeName"),
            "action": "checkout"
        })
    else:
        checkin_count = db.alt_checkins.count_documents({
            "EmployeeId": emp_id, 
            "CheckinDate": checkin_date_str, 
            "CheckType": "checkin"
        })
        return jsonify({
            "message": "✅ Check-in thành công!",
            "EmployeeName": employee.get("EmployeeName"),
            "TodayCount": checkin_count,
            "action": "checkin"
        })

# ---- API: Xin nghỉ phép (GIỮ NGUYÊN) ----
@app.route("/api/leave", methods=["POST"])
def leave_request():
    try: 
        data = request.get_json()
        emp_id = data.get("EmployeeId")

        employee = db.idx_collection.find_one({"EmployeeId": emp_id})
        if not employee: 
            return jsonify({"message": "❌ Mã nhân viên không có"}), 404
        
        department = employee.get("Position")
        if not department or department not in APPROVAL_WORKFLOWS:
            return jsonify({"message": f"❌ Không có quy trình duyệt cho phòng ban '{department}'."}), 400

        now = datetime.now(VN_TZ)
        leave_note = data.get("LeaveNote", "")
        leave_data = {
            "EmployeeId": emp_id, 
            "EmployeeName": employee.get("EmployeeName"),
            "CreationTime": now, 
            "Reason": data.get("Reason"), 
            "LeaveNote": leave_note,
            "ApprovalToken": str(uuid.uuid4()), 
            "ApprovalExpiry": now + timedelta(days=APPROVAL_EXPIRY_DAYS),
            "ApprovalHistory": [], 
            "Department": department,
            "ApprovalWorkflow": APPROVAL_WORKFLOWS[department], 
            "CurrentApproverIndex": 0,
            "Status": "Chờ duyệt",
            "ApprovalDate1": None,
            "Status1": None,
            "ApprovalDate2": None,
            "Status2": None
        }
        
        if data.get("LeaveDate"):
            leave_date, session_val = data.get("LeaveDate"), data.get("Session")
            if not leave_date or not session_val: return jsonify({"message": "❌ Thiếu ngày/buổi nghỉ"}), 400
            if session_val == "Sáng" and datetime.strptime(leave_date, "%Y-%m-%d").date() == now.date() and now.hour >= 12:
                return jsonify({"message": "❌ Không thể xin nghỉ buổi Sáng sau 12h trưa!"}), 400
            leave_data["DisplayDate"] = f"{leave_date} {session_val}"
        else:
            start, end = data.get("StartDate"), data.get("EndDate")
            if not start or not end or start > end: return jsonify({"message": "❌ Ngày nghỉ không hợp lệ"}), 400
            leave_data["DisplayDate"] = f"Từ {start} đến {end}"
        
        db.alt_checkins.insert_one(leave_data)
        
        send_approval_notification(leave_data)
        
        return jsonify({"message": f"✅ Yêu cầu đã được gửi để duyệt!", "EmployeeName": employee.get("EmployeeName")})

    except Exception as e: 
        logger.error(f"!!!!!!!!!!!! UNCAUGHT EXCEPTION in /api/leave: {e}", exc_info=True)
        return jsonify({"message": "Lỗi server nội bộ. Không thể gửi yêu cầu. Vui lòng thử lại sau."}), 500

# ---- Trang xác nhận DUYỆT (GIỮ NGUYÊN) ----
@app.route("/approve/<token>", methods=["GET", "POST"])
@login_required
def approve_leave(token):
    record = db.alt_checkins.find_one({"ApprovalToken": token})
    if not record or record.get("Status", "") != "Chờ duyệt":
        status = record.get("Status", "Không rõ trạng thái") if record else "Không rõ trạng thái"
        approver_name = "Unknown"
        approval_date_str = "N/A"
        if record:
            history = record.get("ApprovalHistory", [])
            if history:
                last_entry = history[-1]
                approver_name = last_entry.get("approver", "Unknown")
                try:
                    approval_date = datetime.strptime(last_entry.get("timestamp", ""), "%d/%m/%Y %H:%M:%S")
                    approval_date_str = approval_date.strftime("%d/%m/%Y %H:%M:%S")
                except (ValueError, TypeError):
                    approval_date_str = last_entry.get("timestamp", "N/A")
        action_text = "đã được xử lý"
        if "duyệt" in status.lower():
            action_text = "được duyệt"
        elif "từ chối" in status.lower():
            action_text = "bị từ chối"

        return f"""
        <!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8"><title>Trạng thái yêu cầu</title><style>
        body{{font-family:Arial,sans-serif; background:#f4f6f9; padding:20px;}}
        .container{{max-width:600px; margin:40px auto; padding:30px; background:white; border-radius:10px; box-shadow:0 2px 10px rgba(0,0,0,0.1);}}
        a{{color:#007bff; text-decoration:none;}}
        </style></head><body><div class="container">
        <h2>📋 Trạng thái yêu cầu</h2>
        <p>Yêu cầu này đã {action_text} bởi <strong>{approver_name}</strong> vào lúc {approval_date_str}.</p>
        <p>Trạng thái cuối cùng: <strong>{status}</strong>.</p>
        <a href="/leave-requests">Quay lại danh sách</a></div></body></html>
        """

    current_admin_email = session.get("email", "").lower()
    workflow = record.get("ApprovalWorkflow", [])
    idx = record.get("CurrentApproverIndex", 0)
    
    if not workflow or current_admin_email != workflow[idx].lower():
        current_approver_name = get_username_from_email(workflow[idx] if workflow else None)
        return f"""
        <!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8"><title>Lỗi</title><style>
        body{{font-family:Arial,sans-serif; background:#f4f6f9; padding:20px;}}
        .container{{max-width:600px; margin:40px auto; padding:30px; background:white; border-radius:10px; box-shadow:0 2px 10px rgba(0,0,0,0.1);}}
        a{{color:#007bff; text-decoration:none;}}
        </style></head><body><div class="container">
        <h2>❌ Lỗi quyền truy cập</h2>
        <p>Bạn không có quyền duyệt yêu cầu này ở bước hiện tại.</p>
        <p>Người duyệt tiếp theo: {current_approver_name}</p>
        <a href="/leave-requests">Quay lại danh sách</a></div></body></html>
        """
    if request.method == "POST":
        action, note, approver_name, now = request.form.get("action"), request.form.get("approve_note", ""), session.get("username"), datetime.now(VN_TZ)
        employee = db.idx_collection.find_one({"EmployeeId": record["EmployeeId"]})
        emp_email, emp_name = employee.get("Email"), employee.get("EmployeeName")
        department = record.get("Department", "N/A")
        leave_note_display = record.get("LeaveNote") or "Không có ghi chú"
        history_entry = {
            "approver": approver_name,
            "action": f"Đã duyệt bởi {approver_name}, {note}" if action == "approve" else f"Đã từ chối bởi {approver_name}, {note}",
            "timestamp": now.strftime("%d/%m/%Y %H:%M:%S")
        }
        db.alt_checkins.update_one({"_id": record["_id"]}, {"$push": {"ApprovalHistory": history_entry}})
        
        if action == "reject":
            department = record.get("Department")
            current_idx = record.get("CurrentApproverIndex", 0)
            update_data = {}

            if department in ["Phòng Kinh Doanh", "Phòng Sales Admin", "Phòng Kỹ Thuật"] and current_idx == 0:
                update_data = {
                    "ApprovalDate2": now,
                    "Status2": f"Đã từ chối bởi {approver_name}, {note}",
                    "Status": f"Đã từ chối bởi {approver_name}"
                }
            
            db.alt_checkins.update_one({"_id": record["_id"]}, {"$set": update_data})
            subject = f"Yêu cầu nghỉ phép bị từ chối - {emp_name}"
            body = f"""
                <h3 style="color: #333; font-size: 20px; margin-bottom: 15px;">Yêu cầu nghỉ phép bị từ chối</h3>
                <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Nhân viên:</strong> {emp_name} ({record['EmployeeId']})</p>
                <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Phòng ban:</strong> {department}</p>
                <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Ngày nghỉ:</strong> {record['DisplayDate']}</p>
                <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Lý do xin nghỉ:</strong> {record['Reason']}</p>
                <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Ghi chú của nhân viên:</strong> {leave_note_display}</p>
                <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Người từ chối:</strong> {approver_name}</p>
                <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Lý do từ chối:</strong> {note}</p>
            """
            if send_email(emp_email, subject, body, cc=[workflow[i] for i in range(idx + 1)]):
                logger.info(f"✅ Rejection email sent to {emp_email} for leave request {token}")
            else:
                logger.error(f"❌ Failed to send rejection email to {emp_email} for leave request {token}")
        
        else: # approve
            is_final_approver = (idx + 1) >= len(workflow)
            
            if not is_final_approver: 
                next_idx = idx + 1
                update_data = {
                    "Status": "Chờ duyệt",
                    "CurrentApproverIndex": next_idx,
                    f"ApprovalDate{idx + 1}": now, 
                    f"Status{idx + 1}": f"Đã duyệt bởi {approver_name}, {note}"
                }
                db.alt_checkins.update_one({"_id": record["_id"]}, {"$set": update_data})
                
                updated_record = db.alt_checkins.find_one({"_id": record["_id"]})
                send_approval_notification(updated_record)
            
            else: 
                update_data = {
                    "Status": f"Đã được duyệt hoàn tất bởi {approver_name}",
                }

                if department in ["Phòng Kinh Doanh", "Phòng Sales Admin", "Phòng Kỹ Thuật"]:
                    update_data["ApprovalDate2"] = now
                    update_data["Status2"] = f"Đã duyệt bởi {approver_name}, {note}"
                else: 
                    update_data[f"ApprovalDate{idx + 1}"] = now 
                    update_data[f"Status{idx + 1}"] = f"Đã duyệt bởi {approver_name}, {note}"
                
                db.alt_checkins.update_one({"_id": record["_id"]}, {"$set": update_data})

                subject = f"Yêu cầu nghỉ phép được duyệt - {emp_name}"
                body = f"""
                    <h3 style="color: #333; font-size: 20px; margin-bottom: 15px;">Yêu cầu nghỉ phép được duyệt hoàn tất</h3>
                    <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Nhân viên:</strong> {emp_name} ({record['EmployeeId']})</p>
                    <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Phòng ban:</strong> {department}</p>
                    <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Ngày nghỉ:</strong> {record['DisplayDate']}</p>
                    <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Lý do xin nghỉ:</strong> {record['Reason']}</p>
                    <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Ghi chú của nhân viên:</strong> {leave_note_display}</p>
                    <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Người duyệt cuối cùng:</strong> {approver_name}</p>
                    <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Ghi chú của người duyệt:</strong> {note}</p>
                """
                final_cc = list(set(FINAL_CC_LISTS.get(record["Department"], []) + [emp_email]))
                send_email(emp_email, subject, body, cc=final_cc)
        
        return f"""
        <!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8"><title>Thành công</title><style>
        body{{font-family:Arial,sans-serif; background:#f4f6f9; padding:20px;}}
        .container{{max-width:600px; margin:40px auto; padding:30px; background:white; border-radius:10px; box-shadow:0 2px 10px rgba(0,0,0,0.1);}}
        a{{color:#007bff; text-decoration:none;}}
        </style></head><body><div class="container">
        <h2>✅ Thao tác thành công</h2>
        <p>Hệ thống đã ghi nhận hành động của bạn và gửi thông báo qua email.</p>
        <a href="/leave-requests">Quay lại danh sách</a></div></body></html>
        """
        
    current_approver_name = get_username_from_email(workflow[idx] if workflow else None)
    first_approver_info = ""
    if idx > 0 and record.get("ApprovalHistory"):
        first_history = record["ApprovalHistory"][0]
        first_approver_name = first_history.get("approver", "Unknown")
        first_approver_timestamp = first_history.get("timestamp", "N/A")
        note_parts = first_history.get("action", "").split(", ", 1)
        first_approver_note = note_parts[1] if len(note_parts) > 1 else "Không có ghi chú"
        first_approver_info = f"""
        <h4>Thông tin duyệt lần đầu:</h4>
        <ul style="list-style-type: none; padding-left: 0;">
            <li><strong>Người duyệt:</strong> {first_approver_name}</li>
            <li><strong>Thời gian:</strong> {first_approver_timestamp}</li>
            <li><strong>Ghi chú:</strong> {first_approver_note}</li>
        </ul>
        """
    leave_note_display = record.get("LeaveNote") or "Không có ghi chú"
    return f"""
    <!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chi tiết đơn nghỉ phép</title><style>
    body{{font-family:Arial,sans-serif; background:#f4f6f9; padding:20px;}}
    .container{{max-width:600px; margin:40px auto; padding:30px; background:white; border-radius:10px; box-shadow:0 2px 10px rgba(0,0,0,0.1);}}
    .info-table th, .info-table td {{padding:10px; border:1px solid #dee2e6; text-align:left;}}
    .info-table th {{background:#f8f9fa; width: 30%;}}
    textarea {{width:100%; min-height:100px; margin-top:10px; border:1px solid #ddd; border-radius:4px; box-sizing: border-box; padding: 8px;}}
    .btn{{padding:12px 24px; border-radius:5px; cursor:pointer; border:none; margin-right:10px; font-size:16px;}}
    .btn-approve{{background:#28a745; color:white;}}
    .btn-reject{{background:#dc3545; color:white;}}
    h2, h4 {{color:#333;}}
    ul {{padding-left:20px;}}
    </style></head><body><div class="container">
        <h2>📋 Chi tiết đơn nghỉ phép</h2>
        <p><strong>Người duyệt hiện tại:</strong> {current_approver_name}</p>
        <table class="info-table" width="100%" style="border-collapse: collapse;">
            <tr><th>Nhân viên</th><td>{record.get('EmployeeName')} ({record.get('EmployeeId')})</td></tr>
            <tr><th>Phòng ban</th><td>{record.get('Department', 'N/A')}</td></tr>
            <tr><th>Ngày nghỉ</th><td>{record.get('DisplayDate', 'N/A')}</td></tr>
            <tr><th>Lý do</th><td>{record.get('Reason', '')}</td></tr>
            <tr><th>Ghi chú nhân viên</th><td>{leave_note_display}</td></tr>
        </table>
        {first_approver_info}
        <form method="POST" style="margin-top: 20px;">
            <label for="approve_note"><strong>Ghi chú duyệt/từ chối (tùy chọn):</strong></label><br>
            <textarea name="approve_note" id="approve_note" placeholder="Nhập ghi chú..."></textarea><br><br>
            <button type="submit" name="action" value="approve" class="btn btn-approve">✅ Duyệt</button>
            <button type="submit" name="action" value="reject" class="btn btn-reject">❌ Từ chối</button>
        </form>
        <a href="/leave-requests" style="display:block; margin-top:20px;">Quay lại danh sách</a>
    </div></body></html>
    """
# # ---- (THÊM MỚI) API TẠM THỜI ĐỂ DỌN DẸP ĐƠN KẸT CÓ STATUS2 ----
# @app.route("/api/fix-stuck-kt-leaves")
# @login_required # Đảm bảo chỉ admin mới chạy được
# def fix_stuck_kt_leaves():
#     """
#     API này tìm các đơn của P.Kỹ Thuật đang "Chờ duyệt" nhưng ĐÃ CÓ
#     dữ liệu ở Status2 (do logic code cũ).
#     Nó sẽ cập nhật Status chính thành "Hoàn tất" và gửi mail.
#     """
#     logger.info(f"--- [ADMIN ACTION] BẮT ĐẦU DỌN DẸP ĐƠN KẸT (CÓ STATUS2) ---")
#     count = 0
    
#     # Tìm các đơn của P.Kỹ Thuật, đang "Chờ duyệt", VÀ ĐÃ CÓ Status2
#     query = {
#         "Department": "Phòng Kỹ Thuật",
#         "Status": "Chờ duyệt",
#         "Status2": {"$exists": True, "$ne": None} 
#     }
    
#     requests_to_fix = list(db.alt_checkins.find(query))
    
#     if not requests_to_fix:
#         logger.info("Không tìm thấy đơn nào của P.Kỹ Thuật bị kẹt (có Status2) cần dọn dẹp.")
#         return "Không tìm thấy đơn nào của P.Kỹ Thuật bị kẹt (có Status2) cần dọn dẹp."

#     logger.info(f"Tìm thấy {len(requests_to_fix)} đơn kẹt (có Status2) cần dọn dẹp...")

#     for record in requests_to_fix:
#         try:
#             emp_name = record.get("EmployeeName", "N/A")
#             emp_id = record.get("EmployeeId", "N/A")
#             record_id = record.get("_id")
            
#             logger.info(f"Đang dọn dẹp đơn {record_id} của {emp_name}...")

#             # 1. Lấy thông tin duyệt từ Status2 (đã có sẵn)
#             status2_data = record.get("Status2", "Đã duyệt (fix)")
            
#             # Tự động lấy tên người duyệt từ Status2
#             approver_name = "Unknown" 
#             if "bởi" in status2_data:
#                 try:
#                     approver_name = status2_data.split("bởi ")[1].split(",")[0].strip()
#                 except Exception:
#                     pass 

#             # 2. Chuẩn bị dữ liệu cập nhật
#             update_data = {
#                 "$set": {
#                     "Status": f"Đã được duyệt hoàn tất bởi {approver_name}", # Cập nhật Status chính
#                 }
#             }

#             # 3. Cập nhật vào Database
#             db.alt_checkins.update_one({"_id": record_id}, update_data)

#             # 4. Gửi email duyệt thành công (vì code cũ có thể đã quên)
#             employee = db.idx_collection.find_one({"EmployeeId": emp_id})
#             emp_email = employee.get("Email") if employee else None
            
#             subject = f"Yêu cầu nghỉ phép được duyệt - {emp_name}"
#             body = f"""
#                 <h3 style="color: #333; font-size: 20px; margin-bottom: 15px;">Yêu cầu nghỉ phép được duyệt hoàn tất</h3>
#                 <p>Yêu cầu này (trước đây đang bị lỗi) đã được dọn dẹp và cập nhật trạng thái Hoàn tất.</p>
#                 <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Nhân viên:</strong> {emp_name} ({emp_id})</p>
#                 <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Phòng ban:</strong> {record.get('Department', 'N/A')}</p>
#                 <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Ngày nghỉ:</strong> {record.get('DisplayDate', 'N/A')}</p>
#                 <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Người duyệt:</strong> {approver_name}</p>
#             """
            
#             final_cc = list(set(FINAL_CC_LISTS.get(record["Department"], []) + [emp_email]))
#             send_email(emp_email, subject, body, cc=final_cc)
            
#             count += 1
#             logger.info(f"✅ Đã dọn dẹp thành công đơn {record_id} của {emp_name}.")

#         except Exception as e:
#             logger.error(f"❌ Lỗi khi dọn dẹp đơn {record.get('_id')}: {e}", exc_info=True)
    
#     logger.info(f"--- [ADMIN ACTION] HOÀN TẤT DỌN DẸP. Đã xử lý: {count} đơn. ---")
#     return f"Đã dọn dẹp và xử lý thành công {count} đơn nghỉ của Phòng Kỹ Thuật (bị kẹt Status2)."

# # ---- (THÊM MỚI) API TẠM THỜI ĐỂ DI DỜI DỮ LIỆU ----
# # Ghi chú: Chỉ chạy API này MỘT LẦN sau khi đã cập nhật APPROVAL_WORKFLOWS
# @app.route("/api/migrate-kt-leaves")
# @login_required # Đảm bảo chỉ admin mới chạy được
# def migrate_kt_leaves():
#     """
#     API này tìm tất cả đơn của P.Kỹ Thuật đang "Chờ duyệt" ở bước 2,
#     sao chép dữ liệu duyệt 1 (của P.H.Nghĩa) sang dữ liệu duyệt 2,
#     cập nhật trạng thái "Hoàn tất" và gửi mail thành công.
#     """
#     logger.info(f"--- [ADMIN ACTION] BẮT ĐẦU DI DỜI DỮ LIỆU ĐƠN NGHỈ PHÒNG KỸ THUẬT ---")
#     count = 0
    
#     # Tìm các đơn của P.Kỹ Thuật, đang "Chờ duyệt", và đang ở bước 2 (index=1)
#     # Tức là đã qua bước 1 (Status1 đã có)
#     query = {
#         "Department": "Phòng Kỹ Thuật",
#         "Status": "Chờ duyệt",
#         "CurrentApproverIndex": 1, 
#         "Status1": {"$exists": True, "$ne": None} 
#     }
    
#     # Dùng list() để lấy hết về, tránh lỗi cursor khi đang loop
#     requests_to_migrate = list(db.alt_checkins.find(query))
    
#     if not requests_to_migrate:
#         logger.info("Không tìm thấy đơn nào của P.Kỹ Thuật cần di dời.")
#         return "Không tìm thấy đơn nào của P.Kỹ Thuật cần di dời."

#     logger.info(f"Tìm thấy {len(requests_to_migrate)} đơn cần di dời...")

#     for record in requests_to_migrate:
#         try:
#             emp_name = record.get("EmployeeName", "N/A")
#             emp_id = record.get("EmployeeId", "N/A")
#             record_id = record.get("_id")
            
#             logger.info(f"Đang xử lý đơn {record_id} của {emp_name}...")

#             # 1. Lấy thông tin duyệt từ bước 1
#             status1_data = record.get("Status1", "Đã duyệt (migration)")
#             approval_date1 = record.get("ApprovalDate1", datetime.now(VN_TZ)) # Lấy ngày duyệt 1
            
#             # Tự động lấy tên người duyệt từ Status1
#             approver_name = "Phạm Hồng Nghĩa" # Mặc định
#             if "bởi" in status1_data:
#                 try:
#                     approver_name = status1_data.split("bởi ")[1].split(",")[0].strip()
#                 except Exception:
#                     pass # Giữ tên mặc định

#             # 2. Chuẩn bị dữ liệu cập nhật
#             update_data = {
#                 "$set": {
#                     "Status": f"Đã được duyệt hoàn tất bởi {approver_name}", # Cập nhật Status chính
#                     "ApprovalDate2": approval_date1,  # Sao chép Date1 -> Date2
#                     "Status2": status1_data,          # Sao chép Status1 -> Status2
#                     # Giữ CurrentApproverIndex = 1 cũng không sao, vì Status đã là "Hoàn tất"
#                 }
#             }

#             # 3. Cập nhật vào Database
#             db.alt_checkins.update_one({"_id": record_id}, update_data)

#             # 4. Gửi email duyệt thành công
#             employee = db.idx_collection.find_one({"EmployeeId": emp_id})
#             emp_email = employee.get("Email") if employee else None
            
#             subject = f"Yêu cầu nghỉ phép được duyệt - {emp_name}"
#             body = f"""
#                 <h3 style="color: #333; font-size: 20px; margin-bottom: 15px;">Yêu cầu nghỉ phép được duyệt hoàn tất</h3>
#                 <p>Yêu cầu này (trước đây đang chờ duyệt bước 2) đã được tự động duyệt và hoàn tất.</p>
#                 <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Nhân viên:</strong> {emp_name} ({emp_id})</p>
#                 <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Phòng ban:</strong> {record.get('Department', 'N/A')}</p>
#                 <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Ngày nghỉ:</strong> {record.get('DisplayDate', 'N/A')}</p>
#                 <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Người duyệt:</strong> {approver_name}</p>
#             """
            
#             # Lấy CC list của P.Kỹ Thuật (đã được cập nhật)
#             final_cc = list(set(FINAL_CC_LISTS.get(record["Department"], []) + [emp_email]))
#             send_email(emp_email, subject, body, cc=final_cc)
            
#             count += 1
#             logger.info(f"✅ Đã di dời thành công đơn {record_id} của {emp_name}.")

#         except Exception as e:
#             logger.error(f"❌ Lỗi khi di dời đơn {record.get('_id')}: {e}", exc_info=True)
    
#     logger.info(f"--- [ADMIN ACTION] HOÀN TẤT DI DỜI. Đã xử lý: {count} đơn. ---")
#     return f"Đã di dời và xử lý thành công {count} đơn nghỉ của Phòng Kỹ Thuật."


# ---- (THÊM MỚI) Khởi động Scheduler ----
# Chỉ chạy khi deploy, không chạy khi debug
if not app.debug:
    try:
        scheduler = BackgroundScheduler(timezone=VN_TZ)
        
        # Thêm lịch nhắc nhở (giờ Việt Nam) - Thứ 2 đến Thứ 6
        # 8:00 sáng
        scheduler.add_job(schedule_checkin_reminder, 'cron', day_of_week='mon-sat', hour=7, minute=50)
        # 17:00 chiều
        scheduler.add_job(schedule_checkout_reminder, 'cron', day_of_week='mon-sat', hour=16, minute=55)
        
        scheduler.add_job(schedule_overdue_leave_reminder, 'cron', day_of_week='mon-sat', hour=9, minute=0)
        
        # (Để test) Chạy 5 phút một lần
        # scheduler.add_job(schedule_checkin_reminder, 'interval', minutes=5)
        # scheduler.add_job(schedule_checkout_reminder, 'interval', minutes=5)

        scheduler.start()
        logger.info("✅ Bộ lập lịch (Scheduler) đã được khởi động.")
        
    except Exception as e:
        logger.error(f"❌ Không thể khởi động Scheduler: {e}", exc_info=True)
# ---- (THÊM MỚI) API CHỮA CHÁY: GỬI LẠI MAIL CHO S031 VÀ S033 ----
@app.route("/api/resend-approval-emails")
@login_required # Đảm bảo chỉ admin đã đăng nhập mới chạy được
def resend_approval_emails():
    """
    API dùng để gửi lại email hoàn tất duyệt cho các nhân viên 
    bị sót mail do trước đó chưa có địa chỉ email trong DB.
    """
    # Danh sách ID nhân viên cần gửi lại (dựa theo hình ảnh bạn cung cấp)
    target_employees = ["S031", "S033"]
    count = 0
    results = []
    
    logger.info("--- [ADMIN ACTION] BẮT ĐẦU GỬI LẠI MAIL DUYỆT ---")

    for emp_id in target_employees:
        # 1. Tìm đơn xin nghỉ mới nhất của nhân viên này đã được duyệt
        record = db.alt_checkins.find_one(
            {
                "EmployeeId": emp_id,
                "Status": {"$regex": "Đã duyệt|Hoàn tất", "$options": "i"}
            },
            sort=[("CreationTime", -1)] # Ưu tiên lấy đơn mới nhất
        )

        if not record:
            results.append(f"⚠️ Không tìm thấy đơn đã duyệt cho mã {emp_id}.")
            continue

        emp_name = record.get("EmployeeName", "N/A")
        department = record.get("Department", "N/A")
        leave_note_display = record.get("LeaveNote") or "Không có ghi chú"

        # 2. Lấy email nhân viên (bạn vừa update)
        employee = db.idx_collection.find_one({"EmployeeId": emp_id})
        emp_email = employee.get("Email") if employee else None

        if not emp_email:
            results.append(f"❌ Nhân viên {emp_name} ({emp_id}) VẪN CHƯA có email trong DB!")
            continue

        # 3. Bóc tách tên người duyệt từ Status (VD: "Đã duyệt bởi Lê Hồng Nhật,")
        status_text = record.get("Status", "")
        approver_name = "Quản lý"
        if "bởi" in status_text:
            try:
                approver_name = status_text.split("bởi ")[1].split(",")[0].strip()
            except Exception:
                pass

        # 4. Lấy ghi chú duyệt từ lịch sử (nếu có)
        note = ""
        if "ApprovalHistory" in record and record["ApprovalHistory"]:
            last_history = record["ApprovalHistory"][-1]
            note_parts = last_history.get("action", "").split(", ", 1)
            note = note_parts[1] if len(note_parts) > 1 else ""

        # 5. Tạo nội dung email giống hệt lúc chốt duyệt
        subject = f"Yêu cầu nghỉ phép được duyệt - {emp_name}"
        body = f"""
            <h3 style="color: #333; font-size: 20px; margin-bottom: 15px;">Yêu cầu nghỉ phép được duyệt hoàn tất</h3>
            <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Nhân viên:</strong> {emp_name} ({emp_id})</p>
            <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Phòng ban:</strong> {department}</p>
            <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Ngày nghỉ:</strong> {record.get('DisplayDate', 'N/A')}</p>
            <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Lý do xin nghỉ:</strong> {record.get('Reason', 'N/A')}</p>
            <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Ghi chú của nhân viên:</strong> {leave_note_display}</p>
            <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Người duyệt cuối cùng:</strong> {approver_name}</p>
            <p style="color: #555; line-height: 1.6; font-size: 16px;"><strong>Ghi chú của người duyệt:</strong> {note}</p>
        """

        # Lấy danh sách CC theo phòng ban và gộp với email nhân viên
        final_cc = list(set(FINAL_CC_LISTS.get(department, []) + [emp_email]))

        # 6. Kích hoạt gửi mail
        success = send_email(emp_email, subject, body, cc=final_cc)

        if success:
            count += 1
            results.append(f"✅ Đã gửi lại mail thành công cho <strong>{emp_name} ({emp_id})</strong> tới email: {emp_email}")
        else:
            results.append(f"❌ Lỗi gửi mail cho {emp_name} ({emp_id}). Hãy kiểm tra log.")

    logger.info(f"--- [ADMIN ACTION] KẾT THÚC. Đã gửi {count} email. ---")
    
    # Hiển thị kết quả ra màn hình trình duyệt
    html_result = "<br><br>".join(results)
    return f"""
    <!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8"><title>Kết quả gửi mail</title>
    <style>body {{ font-family: Arial, sans-serif; padding: 30px; line-height: 1.6; }}</style>
    </head><body>
        <h2>Kết quả gửi lại Email Duyệt</h2>
        <div>{html_result}</div>
        <hr>
        <p><b>Tổng số email đã gửi: {count}/2</b></p>
        <a href="/leave-requests" style="display:inline-block; margin-top:20px; padding:10px 20px; background:#007bff; color:white; text-decoration:none; border-radius:5px;">Trở về trang chủ</a>
    </body></html>
    """

# if __name__ == "__main__":
#     # Tắt reloader để scheduler chạy ổn định trong môi trường phát triển
#     app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
