import os
import requests
from pymongo import MongoClient

# --- 1. CẤU HÌNH ---
RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
RESEND_FROM_EMAIL = os.environ.get('RESEND_FROM_EMAIL')
MONGO_URI = os.environ.get('MONGO_URI')
DB_NAME = os.environ.get('DB_NAME')
# Domain của bạn: sun-chi-roan.up.railway.app
SERVER_NAME = os.environ.get('SERVER_NAME', 'sun-chi-roan.up.railway.app')

# Kết nối DB
client = MongoClient(MONGO_URI)
db = client[DB_NAME]

# --- 2. HÀM GỬI EMAIL ---
def send_email_manual(to_email, subject, body, cc=None):
    try:
        to_list = [to_email] if isinstance(to_email, str) else to_email
        payload = {
            "from": f"Sun Automation System <{RESEND_FROM_EMAIL}>",
            "to": to_list,
            "subject": subject,
            "html": f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #eee; border-radius: 10px;">
                <div style="background-color: #007bff; padding: 15px; border-radius: 8px 8px 0 0; text-align: center;">
                    <h1 style="color: #ffffff; margin: 0; font-size: 24px;">Sun Automation System</h1>
                </div>
                <div style="padding: 20px;">{body}</div>
            </div>
            """
        }
        if cc: payload["cc"] = cc
        
        headers = {
            "Authorization": f"Bearer {RESEND_API_KEY}", 
            "Content-Type": "application/json"
        }
        response = requests.post("https://api.resend.com/emails", json=payload, headers=headers)
        return response.status_code == 200
    except Exception as e:
        print(f"Lỗi gửi mail: {e}")
        return False

# --- 3. LOGIC GỬI THÔNG BÁO DUYỆT ---
def trigger_manual_notification(employee_id):
    # Tìm bản ghi trong database
    record = db.alt_checkins.find_one({"EmployeeId": employee_id, "Status": "Chờ duyệt"})
    
    if not record:
        print(f"❌ Không tìm thấy đơn 'Chờ duyệt' cho nhân viên {employee_id}")
        return

    workflow = record.get("ApprovalWorkflow", [])
    current_idx = record.get("CurrentApproverIndex", 0)
    current_approver_email = workflow[current_idx]
    
    emp_name = record.get("EmployeeName")
    display_date = record.get("DisplayDate")
    reason = record.get("Reason")
    token = record.get("ApprovalToken")
    
    # --- SỬA LỖI TẠI ĐÂY: Tạo Link duyệt thủ công bằng chuỗi ---
    detail_url = f"https://{SERVER_NAME}/approve/{token}"

    subject = f"Yêu cầu duyệt nghỉ phép - {emp_name}"
    body = f"""
        <h3 style="color: #333;">Yêu cầu nghỉ phép cần bạn duyệt</h3>
        <p><strong>Nhân viên:</strong> {emp_name} ({employee_id})</p>
        <p><strong>Ngày nghỉ:</strong> {display_date}</p>
        <p><strong>Lý do:</strong> {reason}</p>
        <p><strong>Ghi chú:</strong> {record.get('LeaveNote', 'Không có')}</p>
        <p style="margin: 25px 0;">
            <a href="{detail_url}" style="display: inline-block; padding: 12px 24px; background-color: #007bff; color: #ffffff; text-decoration: none; border-radius: 5px; font-weight: bold;">Xử lý yêu cầu</a>
        </p>
        <p style="color: #666; font-size: 13px;">Nếu nút trên không hoạt động, bạn có thể copy link sau dán vào trình duyệt:<br>{detail_url}</p>
    """

    # Lấy email cá nhân của nhân viên để CC
    employee_info = db.idx_collection.find_one({"EmployeeId": employee_id})
    cc_list = [employee_info.get("Email")] if employee_info and employee_info.get("Email") else None

    if send_email_manual(current_approver_email, subject, body, cc=cc_list):
        print(f"✅ Đã gửi mail duyệt đơn của {emp_name} đến {current_approver_email}")
    else:
        print(f"❌ Gửi mail thất bại cho {emp_name}")

# --- 4. CHẠY GỬI CHO 2 USER ---
if __name__ == "__main__":
    print("--- Bắt đầu gửi mail thủ công ---")
    
    # Gửi cho Đặng Danh Hiệu
    trigger_manual_notification("S006")
    
    # Gửi cho Nguyễn Thị Minh Sự
    trigger_manual_notification("S017")
    
    print("--- Hoàn tất ---")
