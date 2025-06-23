#include <Servo.h> // Thêm thư viện Servo có sẵn trong Arduino IDE

// Tạo một đối tượng servo để điều khiển
Servo myServo;

// Biến để lưu vị trí (góc) của servo
int angle = 0;

void setup() {
  // Gắn đối tượng servo vào chân số 9 trên board Arduino
  // Bạn có thể thay đổi chân 9 thành bất kỳ chân PWM nào khác
  myServo.attach(9);

  // Khởi động giao tiếp Serial để theo dõi giá trị góc trên máy tính
  Serial.begin(9600);
  Serial.println("Bat dau kiem tra Servo SG90...");
}

void loop() {
  // --- QUAY TỪ 0 ĐẾN 180 ĐỘ ---
  Serial.println("Quay theo chieu thuan:");
  for (angle = 0; angle <= 180; angle++) {
    // Gửi lệnh để servo di chuyển đến vị trí 'angle'
    myServo.write(angle);

    // In giá trị góc ra Serial Monitor
    Serial.print("Goc: ");
    Serial.println(angle);

    // Đợi 15ms để servo có thời gian di chuyển đến vị trí mới
    // Việc này giúp servo quay mượt hơn
    delay(15);
  }

  // Đợi 1 giây sau khi hoàn thành một vòng quay
  delay(1000);

  // --- QUAY TỪ 180 VỀ 0 ĐỘ ---
  Serial.println("Quay theo chieu nguoc lai:");
  for (angle = 180; angle >= 0; angle--) {
    // Gửi lệnh để servo di chuyển đến vị trí 'angle'
    myServo.write(angle);

    // In giá trị góc ra Serial Monitor
    Serial.print("Goc: ");
    Serial.println(angle);

    // Đợi 15ms
    delay(15);
  }

  // Đợi 1 giây trước khi lặp lại
  delay(1000);
}