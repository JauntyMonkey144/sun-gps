// --- BIẾN TOÀN CỤC ---
let map, marker, currentLat, currentLng, currentAddress, capturedImage = null;
let empDataGlobal = null;
let actionType = null;
let selectedCheckType = null;
let checkinState = 0; // 0: Bắt đầu, 1: Đã chọn CheckIn/Out, 2: Sẵn sàng gửi
let cameraStream = null; // Biến để quản lý luồng camera

// ============================================================
//  ĐA NGÔN NGỮ (i18n) - VI / EN  (chỉ thêm chức năng, không đổi giao diện)
// ============================================================
const I18N = {
  vi: {
    app_subtitle: "Chấm công GPS", title_checkin: "Chấm công", title_leave: "Tạo đơn xin nghỉ",
    emp_id_label: "Mã nhân viên:", emp_id_ph: "Nhập mã nhân viên",
    opt_checkin: "Chấm công", opt_leave: "Xin nghỉ phép", btn_continue: "Tiếp tục",
    btn_check_in: "Check In", btn_check_out: "Check Out",
    project_id: "ID dự án:", project_id_ph: "Nhập mã dự án...",
    tasks_label: "Công việc:",
    task_meet: "Đi gặp khách hàng", task_survey: "Khảo sát", task_quote: "Báo giá",
    task_install: "Thi công", task_accept: "Nghiệm thu", task_maintain: "Bảo trì",
    task_office: "Office", task_delivery: "Giao hàng", task_other: "Khác:",
    task_other_ph: "Nhập công việc khác...",
    note_label: "Ghi chú:", note_checkin_ph: "Thêm ghi chú cho lần chấm công...",
    note_leave_ph: "Thêm ghi chú cho đơn nghỉ phép...",
    btn_capture: "📷 Chụp ảnh", btn_retake: "🔄 Chụp lại",
    leave_type: "Loại nghỉ phép:", leave_single: "Nghỉ 1 ngày", leave_multi: "Nghỉ nhiều ngày",
    leave_date: "Ngày nghỉ:", session_label: "Buổi:",
    session_morning: "Sáng", session_afternoon: "Chiều", session_full: "Cả ngày",
    from_date: "Từ ngày:", to_date: "Đến ngày:",
    leave_reason: "Lý do nghỉ phép:", reason_health: "Lý do sức khoẻ",
    reason_personal: "Việc cá nhân", reason_other: "Lý do khác:", reason_other_ph: "Nhập lý do khác...",
    btn_confirm: "✅ Xác nhận", btn_cancel: "❌ Hủy",
    emp_greet: "👤 {name} (Mã: {id})",
    count_line: "Hôm nay: Check-in {cin}/9 · Check-out {cout}/1",
    already_checkout: "Đã check-out — không thể check-in lại hôm nay.",
    today_count_lbl: "Số lần hôm nay",
    addr_prefix: "📍 Vị trí: "
  },
  en: {
    app_subtitle: "GPS attendance", title_checkin: "Check-in", title_leave: "Leave request",
    emp_id_label: "Employee ID:", emp_id_ph: "Enter employee ID",
    opt_checkin: "Check-in", opt_leave: "Request leave", btn_continue: "Continue",
    btn_check_in: "Check In", btn_check_out: "Check Out",
    project_id: "Project ID:", project_id_ph: "Enter project ID...",
    tasks_label: "Tasks:",
    task_meet: "Meet customer", task_survey: "Survey", task_quote: "Quotation",
    task_install: "Installation", task_accept: "Acceptance", task_maintain: "Maintenance",
    task_office: "Office", task_delivery: "Delivery", task_other: "Other:",
    task_other_ph: "Enter other task...",
    note_label: "Note:", note_checkin_ph: "Add a note for this check-in...",
    note_leave_ph: "Add a note for the leave request...",
    btn_capture: "📷 Take photo", btn_retake: "🔄 Retake",
    leave_type: "Leave type:", leave_single: "One day", leave_multi: "Multiple days",
    leave_date: "Leave date:", session_label: "Session:",
    session_morning: "Morning", session_afternoon: "Afternoon", session_full: "Full day",
    from_date: "From date:", to_date: "To date:",
    leave_reason: "Reason for leave:", reason_health: "Health reason",
    reason_personal: "Personal matter", reason_other: "Other reason:", reason_other_ph: "Enter other reason...",
    btn_confirm: "✅ Confirm", btn_cancel: "❌ Cancel",
    emp_greet: "👤 {name} (ID: {id})",
    count_line: "Today: Check-in {cin}/9 · Check-out {cout}/1",
    already_checkout: "Already checked out — cannot check in again today.",
    today_count_lbl: "Today's count",
    addr_prefix: "📍 Location: "
  }
};
function detectLang() {
  const saved = localStorage.getItem("appLang");
  if (saved === "vi" || saved === "en") return saved;
  return "vi"; // mặc định Tiếng Việt
}
let currentLang = detectLang();
function t(key, vars) {
  let s = (I18N[currentLang] && I18N[currentLang][key]) || I18N.vi[key] || key;
  if (vars) for (const k in vars) s = s.replace(new RegExp("{" + k + "}", "g"), vars[k]);
  return s;
}
function applyLanguage(lang) {
  currentLang = (lang === "en") ? "en" : "vi";
  localStorage.setItem("appLang", currentLang);
  document.documentElement.lang = currentLang;
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const key = el.getAttribute("data-i18n");
    if (I18N[currentLang][key] !== undefined) el.innerHTML = I18N[currentLang][key];
  });
  document.querySelectorAll("[data-i18n-ph]").forEach(el => {
    const key = el.getAttribute("data-i18n-ph");
    if (I18N[currentLang][key] !== undefined) el.placeholder = I18N[currentLang][key];
  });
  const sel = document.querySelector('input[name="type"]:checked');
  const titleEl = document.getElementById("formTitle");
  if (sel && titleEl) titleEl.innerText = (sel.value === "checkin") ? t("title_checkin") : t("title_leave");
  document.querySelectorAll("#langSwitch button").forEach(b => {
    b.classList.toggle("active", b.getAttribute("data-lang") === currentLang);
  });
}

// --- CÁC HÀM TIỆN ÍCH ---
function showStatusMessage(msg, isError = false) {
    const statusEl = document.getElementById("status");
    statusEl.innerText = msg;
    statusEl.style.color = isError ? "red" : "green";
    // Thông báo kết quả hiển thị trong 10 giây
    setTimeout(() => { statusEl.innerText = ""; }, 10000);
}

/** Hiển thị số lần check-in / check-out trong ngày của nhân viên */
function renderEmpCount(emp) {
    const box = document.getElementById("empCount");
    if (!box) return;
    const cin = emp.CheckinCount || 0;
    const cout = emp.CheckoutCount || 0;
    let text = t("count_line", { cin: cin, cout: cout });
    if (emp.HasCheckedOut) text += " — " + t("already_checkout");
    box.innerText = text;
    box.style.display = "block";
}

/** Tắt luồng camera đang hoạt động */
function stopCamera() {
    if (cameraStream) {
        cameraStream.getTracks().forEach(track => track.stop());
        cameraStream = null;
    }
}

// --- ĐÃ CẬP NHẬT: Lấy địa chỉ từ HERE Maps API ---
async function getAddress(lat, lng) {
    // Khoá HERE nằm ở server. Trình duyệt chỉ gửi toạ độ và nhận lại địa chỉ.
    try {
        const res = await fetch(`/api/geocode?lat=${lat}&lng=${lng}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        return data.address || `(${lat.toFixed(6)}, ${lng.toFixed(6)})`;
    } catch (err) {
        console.error("Lỗi lấy địa chỉ:", err);
        return `(${lat.toFixed(6)}, ${lng.toFixed(6)})`;
    }
}

// --- ĐÃ CẬP NHẬT: Hiển thị bản đồ chi tiết và khắc phục lỗi ngôn ngữ ---
function showMap(lat, lng) {
    document.getElementById("map").style.display = "block";

    // Leaflet làm khung hiển thị, ảnh bản đồ lấy từ HERE qua proxy của server.
    if (!map) {
        map = L.map('map').setView([lat, lng], 18);

        // Tile của HERE, lấy qua server nên không cần API key ở đây
        L.tileLayer('/api/tiles/{z}/{x}/{y}.png', {
            maxZoom: 20,
            tileSize: 512,
            zoomOffset: -1,
            attribution: '&copy; HERE'
        }).addTo(map);

        marker = L.marker([lat, lng]).addTo(map);

        window.addEventListener('resize', () => map.invalidateSize());
        setTimeout(() => map.invalidateSize(), 100);

    } else {
        map.setView([lat, lng], 18);
        marker.setLatLng([lat, lng]);
    }
}

function resetCheckTypeButtons() {
    const checkinBtn = document.getElementById('startCheckinBtn');
    const checkoutBtn = document.getElementById('startCheckoutBtn');
    if(checkinBtn && checkoutBtn) {
        checkinBtn.disabled = false;
        checkoutBtn.disabled = false;
        checkinBtn.classList.remove('selected');
        checkoutBtn.classList.remove('selected');
    }
    selectedCheckType = null;
}

function updateCheckTypeSelection(selectionType) {
    const checkinBtn = document.getElementById('startCheckinBtn');
    const checkoutBtn = document.getElementById('startCheckoutBtn');

    selectedCheckType = selectionType;

    if (selectionType === 'checkin') {
        checkinBtn.classList.add('selected');
        checkoutBtn.classList.remove('selected');
    } else {
        checkoutBtn.classList.add('selected');
        checkinBtn.classList.remove('selected');
    }

    if (checkinState === 0) {
        checkinState = 1;
    }
}

function resetForm() {
    // 1. Tắt luồng camera đang hoạt động (nếu có)
    stopCamera();

    // 2. Reset tất cả các trường input trong form về mặc định
    document.getElementById("checkinForm").reset();

    // 3. Ẩn tất cả các box và section không cần thiết
    document.getElementById("confirmBox").style.display = "none";
    document.getElementById("checkinSection").style.display = "none";
    document.getElementById("leaveSection").style.display = "none";
    document.getElementById("map").style.display = "none";

    // 4. Dọn dẹp các giá trị đã nhập cho lần chấm công
    document.getElementById("checkinNote").value = "";
    document.getElementById("projectId").value = "";
    document.querySelectorAll(".taskCheck").forEach(cb => cb.checked = false);
    document.getElementById("otherTaskCheck").checked = false;
    document.getElementById("otherTaskInput").style.display = "none";
    document.getElementById("otherTaskInput").value = "";

    // 5. Reset trạng thái chụp ảnh
    capturedImage = null;
    if (document.getElementById("preview")) document.getElementById("preview").style.display = "none";
    if (document.getElementById("video")) document.getElementById("video").style.display = "block";
    document.getElementById("retakeBtn").style.display = "none";
    document.getElementById("captureBtn").style.display = "inline-block";

    // 6. Reset các nút Check In/Out
    document.getElementById("checkInOutBox").style.display = "none";
    resetCheckTypeButtons();

    // 7. Ẩn dòng số lần check-in/out
    const ec = document.getElementById("empCount");
    if (ec) ec.style.display = "none";

    // 8. Reset trạng thái và tên nút "Xác nhận" về ban đầu
    checkinState = 0;
    document.getElementById('confirmBtn').innerHTML = t("btn_confirm");
}

// --- KHỞI TẠO VÀ LẮNG NGHE SỰ KIỆN ---



// (SỬA LỖI) BỌC TẤT CẢ CÁC LISTENER VÀO TRONG "DOMContentLoaded"
// Code sẽ chỉ chạy sau khi toàn bộ HTML đã được tải xong.
document.addEventListener("DOMContentLoaded", function() {

    // --- Áp dụng ngôn ngữ (mặc định Tiếng Việt) + nút chuyển VI/EN ---
    applyLanguage(currentLang);
    document.querySelectorAll("#langSwitch button").forEach(btn => {
        btn.addEventListener("click", () => applyLanguage(btn.getAttribute("data-lang")));
    });

    // --- Cài đặt ngày tối thiểu cho lịch ---
    const today = new Date();
    const todayString = today.toISOString().split('T')[0];
    const leaveDateInput = document.getElementById('leaveDate');
    const startDateInput = document.getElementById('startDate');
    const endDateInput = document.getElementById('endDate');
    leaveDateInput.min = todayString;
    startDateInput.min = todayString;
    endDateInput.min = todayString;
    startDateInput.addEventListener('change', function() {
        if (startDateInput.value) { endDateInput.min = startDateInput.value; }
    });

    // --- Gán sự kiện cho các nút ---
    document.getElementById('startCheckinBtn').addEventListener('click', () => updateCheckTypeSelection('checkin'));
    document.getElementById('startCheckoutBtn').addEventListener('click', () => updateCheckTypeSelection('checkout'));

    // --- Gán sự kiện "change" chung ---
    document.addEventListener("change", function (e) {
        if (e.target.name === "leaveReasonRadio") {
            const otherInput = document.getElementById("otherReasonInput");
            if (e.target.value === "Khác") {
                otherInput.style.display = "block"; otherInput.required = true; otherInput.focus();
            } else {
                otherInput.style.display = "none"; otherInput.required = false; otherInput.value = "";
            }
        }
        if (e.target.id === "otherTaskCheck") {
            const otherInput = document.getElementById("otherTaskInput");
            if (e.target.checked) {
                otherInput.style.display = "block"; otherInput.required = true; otherInput.focus();
            } else {
                otherInput.style.display = "none"; otherInput.required = false; otherInput.value = "";
            }
        }
        if (e.target.name === "leaveType") {
            const isSingle = e.target.value === "single";
            document.getElementById("singleDaySection").style.display = isSingle ? "block" : "none";
            document.getElementById("multiDaySection").style.display = isSingle ? "none" : "block";
            document.getElementById("leaveDate").required = isSingle;
            document.getElementById("startDate").required = !isSingle;
            document.getElementById("endDate").required = !isSingle;
        }
    });

    // --- Gán sự kiện cho radio Chấm công/Xin nghỉ ---
    document.querySelectorAll('input[name="type"]').forEach(radio => {
        radio.addEventListener("change", function () {
            document.getElementById("formTitle").innerText = (this.value === "checkin") ? t("title_checkin") : t("title_leave");
        });
    });

    // --- Gán sự kiện Submit Form (Nơi gọi Firebase) ---
    document.getElementById("checkinForm").addEventListener("submit", async function (e) {
        e.preventDefault();
        const empId = document.getElementById("empId").value.trim();
        actionType = document.querySelector('input[name="type"]:checked').value;
        const res = await fetch("/api/checkin-preview", {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ EmployeeId: empId })
        });
        empDataGlobal = await res.json();
        if (!res.ok) {
            showStatusMessage(empDataGlobal.message || "❌ Mã NV không hợp lệ.", true);
            document.getElementById("confirmBox").style.display = "none";
            return;
        }
        document.getElementById("empInfo").innerText = t("emp_greet", { name: empDataGlobal.EmployeeName, id: empDataGlobal.EmployeeId });

        // *** (ĐÃ SỬA) GỌI HÀM INIT FIREBASE TẠI ĐÂY ***
        // Chỉ gọi sau khi đã có empId hợp lệ
        if (typeof window.initFirebaseMessaging === 'function') {
            window.initFirebaseMessaging(empId);
        } else {
            console.warn("Hàm initFirebaseMessaging (từ script module) chưa được tải.");
        }
        // *** HẾT PHẦN SỬA ***

        if (actionType === "checkin") {
            renderEmpCount(empDataGlobal); // Hiển thị số lần check-in/check-out hôm nay
            document.getElementById("addrInfo").innerText = "";
            document.getElementById("map").style.display = "none";
            document.getElementById("checkinSection").style.display = "none";
            document.getElementById("leaveSection").style.display = "none";
            document.getElementById("checkInOutBox").style.display = "flex";
            document.getElementById("confirmBox").style.display = "block";
        } else if (actionType === "leave") {
            const ec = document.getElementById("empCount");
            if (ec) ec.style.display = "none";
            document.getElementById("addrInfo").innerText = "";
            document.getElementById("map").style.display = "none";
            document.getElementById("checkInOutBox").style.display = "none";
            document.getElementById("checkinSection").style.display = "none";
            document.getElementById("leaveSection").style.display = "block";
            document.getElementById("confirmBox").style.display = "block";
            document.getElementById("singleDaySection").style.display = "block";
            document.getElementById("multiDaySection").style.display = "none";
        }
    });

    // --- Gán sự kiện cho các nút Camera ---
    document.getElementById("captureBtn").addEventListener("click", function (e) {
        e.preventDefault();
        const video = document.getElementById("video");
        const canvas = document.getElementById("canvas");
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const context = canvas.getContext("2d");
        context.scale(-1, 1);
        context.drawImage(video, 0, 0, -canvas.width, canvas.height);
        context.scale(1, 1); // (Sửa lỗi lật ảnh)
        capturedImage = canvas.toDataURL("image/jpeg", 0.7);
        video.style.display = "none";
        document.getElementById("preview").src = capturedImage;
        document.getElementById("preview").style.display = "block";
        document.getElementById("captureBtn").style.display = "none";
        document.getElementById("retakeBtn").style.display = "inline-block";
    });

    document.getElementById("retakeBtn").addEventListener("click", function (e) {
        e.preventDefault();
        capturedImage = null;
        document.getElementById("preview").style.display = "none";
        document.getElementById("video").style.display = "block";
        document.getElementById("retakeBtn").style.display = "none";
        document.getElementById("captureBtn").style.display = "inline-block";
    });

    // --- Gán sự kiện cho nút Xác nhận (Confirm) ---
    document.getElementById("confirmBtn").addEventListener("click", async function () {
        const confirmBtn = document.getElementById("confirmBtn");
        const cancelBtn = document.getElementById("cancelBtn");
        confirmBtn.disabled = true;
        cancelBtn.disabled = true;
        const reEnableButtons = () => { confirmBtn.disabled = false; cancelBtn.disabled = false; };

        if (actionType === "checkin") {
            if (checkinState === 1) {
                document.getElementById('startCheckinBtn').disabled = true;
                document.getElementById('startCheckoutBtn').disabled = true;

                navigator.geolocation.getCurrentPosition(async (position) => {
                    currentLat = position.coords.latitude;
                    currentLng = position.coords.longitude;
                    showMap(currentLat, currentLng);
                    currentAddress = await getAddress(currentLat, currentLng);
                    document.getElementById("addrInfo").innerText = t("addr_prefix") + currentAddress;
                    document.getElementById("checkinSection").style.display = "block";
                    navigator.mediaDevices.getUserMedia({ video: true })
                        .then(stream => {
                            cameraStream = stream;
                            document.getElementById("video").srcObject = stream;
                        })
                        .catch(err => { console.error("Lỗi camera:", err); });

                    checkinState = 2;
                    reEnableButtons();

                }, (err) => {
                    showStatusMessage("❌ Không thể lấy vị trí.", true);
                    resetCheckTypeButtons();
                    reEnableButtons();
                }, { enableHighAccuracy: true });
            }
            else if (checkinState === 2) {
                if (!capturedImage) {
                    showStatusMessage("❌ Vui lòng chụp ảnh trước khi hoàn tất!", true);
                    reEnableButtons();
                    return;
                }
                const tasks = Array.from(document.querySelectorAll(".taskCheck:checked")).map(cb => cb.value);
                if (document.getElementById("otherTaskCheck").checked) {
                    const otherTaskValue = document.getElementById("otherTaskInput").value.trim();
                    if (!otherTaskValue) {
                        showStatusMessage("❌ Vui lòng nhập nội dung cho công việc 'Khác'!", true);
                        reEnableButtons(); return;
                    }
                    tasks.push(otherTaskValue);
                }
                const data = {
                    EmployeeId: empDataGlobal.EmployeeId, CheckType: selectedCheckType,
                    Latitude: currentLat, Longitude: currentLng, FaceImage: capturedImage,
                    ProjectId: document.getElementById("projectId").value.trim(),
                    Tasks: tasks, CheckinNote: document.getElementById("checkinNote").value.trim()
                };
                fetch("/api/checkin", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
                    .then(res => res.json().then(payload => ({ ok: res.ok, payload })))
                    .then(({ok, payload}) => {
                        if (!ok) throw new Error(payload.message || "Lỗi không xác định");
                        const message = payload.action === 'checkout' ? `${payload.message} | 👤 ${payload.EmployeeName}` : `${payload.message} | 👤 ${payload.EmployeeName} | ${t("today_count_lbl")}: ${payload.TodayCount}/9`;
                        showStatusMessage(message);
                    })
                    .catch(err => showStatusMessage("❌ " + err.message, true))
                    .finally(() => { resetForm(); reEnableButtons(); });
            }
        }
        else if (actionType === "leave") {
            const leaveNote = document.getElementById("leaveNote").value.trim();
            const leaveType = document.querySelector('input[name="leaveType"]:checked').value;
            const selectedRadio = document.querySelector('input[name="leaveReasonRadio"]:checked');
            if (!selectedRadio) {
                showStatusMessage("❌ Vui lòng chọn lý do nghỉ phép!", true); reEnableButtons(); return;
            }
            let leaveReason = selectedRadio.value;
            if (leaveReason === "Khác") {
                leaveReason = document.getElementById("otherReasonInput").value.trim();
                if (!leaveReason) {
                    showStatusMessage("❌ Vui lòng nhập lý do nghỉ phép!", true); reEnableButtons(); return;
                }
            }
            let data;
            if (leaveType === "single") {
                const leaveDate = document.getElementById("leaveDate").value;
                const session = document.querySelector('input[name="session"]:checked')?.value;
                if (!leaveDate) { showStatusMessage("❌ Vui lòng chọn ngày nghỉ!", true); reEnableButtons(); return; }
                if (!session) { showStatusMessage("❌ Vui lòng chọn buổi!", true); reEnableButtons(); return; }
                data = { EmployeeId: empDataGlobal.EmployeeId, LeaveDate: leaveDate, Session: session, Reason: leaveReason, LeaveNote: leaveNote };
            } else {
                const startDate = document.getElementById("startDate").value;
                const endDate = document.getElementById("endDate").value;
                if (!startDate || !endDate) { showStatusMessage("❌ Vui lòng chọn đầy đủ ngày bắt đầu và ngày kết thúc!", true); reEnableButtons(); return; }
                if (new Date(startDate) > new Date(endDate)) { showStatusMessage("❌ Ngày bắt đầu phải nhỏ hơn hoặc bằng ngày kết thúc!", true); reEnableButtons(); return; }
                data = { EmployeeId: empDataGlobal.EmployeeId, StartDate: startDate, EndDate: endDate, Reason: leaveReason, LeaveNote: leaveNote };
            }
            fetch("/api/leave", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
                .then(res => res.json().then(payload => ({ ok: res.ok, payload })))
                .then(({ok, payload}) => {
                    if (!ok) throw new Error(payload.message || "Lỗi không xác định");
                    showStatusMessage(`${payload.message} | 👤 ${payload.EmployeeName}`);
                })
                .catch(err => showStatusMessage("❌ " + err.message, true))
                .finally(() => { resetForm(); reEnableButtons(); });
        }
    });

    // --- Gán sự kiện cho nút Hủy (Cancel) ---
    document.getElementById("cancelBtn").addEventListener("click", function () {
        showStatusMessage("⚠️ Bạn đã hủy thao tác.", true);
        stopCamera();
        checkinState = 0;
        resetForm();
    });

}); // <-- Kết thúc khối "DOMContentLoaded"
