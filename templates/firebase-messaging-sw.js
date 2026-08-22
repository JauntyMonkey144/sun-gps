// (MỚI) Dùng API v8 (namespaced) cho Service Worker
importScripts("https://www.gstatic.com/firebasejs/8.10.0/firebase-app.js");
importScripts("https://www.gstatic.com/firebasejs/8.10.0/firebase-messaging.js");

// Dùng config mới của bạn
const firebaseConfig = {{ firebase_config | safe }};

// (MỚI) Khởi tạo Firebase theo kiểu v8
if (!firebase.apps.length) {
   firebase.initializeApp(firebaseConfig);
}

// (MỚI) Lấy messaging theo kiểu v8
const messaging = firebase.messaging();

// Hàm xử lý thông báo nền (giữ nguyên)
messaging.onBackgroundMessage((payload) => {
  console.log(
    "[firebase-messaging-sw.js] Received background message ",
    payload
  );

  const notificationTitle = payload.notification.title;
  const notificationOptions = {
    body: payload.notification.body,
    icon: '/static/favicon.ico' // (Tùy chọn) Đặt icon của bạn ở đây
  };

  self.registration.showNotification(notificationTitle, notificationOptions);
});

