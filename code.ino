/**
 * =================================================================
 * HỆ THỐNG KIỂM SOÁT RA VÀO SỬ DỤNG 2 ĐẦU ĐỌC RFID & 2 SERVO
 * =================================================================
 * Tác giả: Dựa trên code của người dùng
 * Ngày: 23/06/2025
 * * Chức năng:
 * - Sử dụng 2 đầu đọc RFID MFRC522 (một cho lối vào, một cho lối ra).
 * - Sử dụng 2 servo SG90 để điều khiển cửa/rào chắn.
 * - Giao tiếp với một ứng dụng Python qua cổng Serial để xác thực thẻ.
 * - Arduino gửi thông tin thẻ đến Python theo định dạng: "[IN/OUT]:UID:[Mã Thẻ]".
 * - Python phản hồi bằng "ALLOW" (cho phép) hoặc "DENY" (từ chối).
 * - Arduino điều khiển servo, đèn LED và còi buzzer dựa trên phản hồi từ Python.
 * * Sơ đồ kết nối (Arduino Mega):
 * -----------------------------------------------------------------
 * | MFRC522 #1 & #2 | Servo #1 | Servo #2 | LED #1 | LED #2 | Buzzer
 * -----------------------------------------------------------------
 * VCC (5V)    | 3.3V (Quan trọng!) | 5V       | 5V       | -      | -      | -
 * GND         | GND               | GND      | GND      | GND    | GND    | GND
 * -----------------------------------------------------------------
 * RST         | Pin 8             | -        | -        | -      | -      | -
 * SDA (SS)    | Pin 7 (#1), Pin 6 (#2) | -    | -        | -      | -      | -
 * MOSI        | Pin 51 (MOSI)     | -        | -        | -      | -      | -
 * MISO        | Pin 50 (MISO)     | -        | -        | -      | -      | -
 * SCK         | Pin 52 (SCK)      | -        | -        | -      | -      | -
 * Signal      | -                 | Pin 9    | Pin 10   | -      | -      | -
 * Anode (+)   | -                 | -        | -        | Pin 3  | Pin 4  | Pin 2
 * -----------------------------------------------------------------
 * LƯU Ý: MFRC522 hoạt động ở mức logic 3.3V. Hãy cấp nguồn 3.3V cho nó, 
 * KHÔNG sử dụng nguồn 5V để tránh làm hỏng module.
 */

#include <SPI.h>
#include <MFRC522.h>
#include <Servo.h>

// --- CẤU HÌNH HỆ THỐNG ---

// Chân điều khiển chung
#define RST_PIN             8       // Chân Reset cho cả 2 module MFRC522
#define BUZZER_PIN          2       // Chân điều khiển còi buzzer

// Cấu hình Reader 1 (Lối vào - IN)
#define SS_1_PIN            7       // Chân SS (SDA) cho Reader 1
#define SERVO_1_PIN         9       // Chân tín hiệu Servo 1
#define LED_1_PIN           3       // Chân đèn LED báo hiệu cho Reader 1

// Cấu hình Reader 2 (Lối ra - OUT)
#define SS_2_PIN            6       // Chân SS (SDA) cho Reader 2
#define SERVO_2_PIN         10      // Chân tín hiệu Servo 2
#define LED_2_PIN           4       // Chân đèn LED báo hiệu cho Reader 2

// Cài đặt hoạt động
#define NR_OF_READERS       2       // Tổng số đầu đọc
#define SERVO_OPEN_ANGLE    90      // Góc servo khi mở cửa
#define SERVO_CLOSE_ANGLE   0       // Góc servo khi đóng cửa
#define DOOR_OPEN_TIME      3000    // Thời gian cửa mở (3 giây)
#define RESPONSE_TIMEOUT    5000    // Thời gian chờ phản hồi từ Python (5 giây)

// Mảng để quản lý các thiết bị
byte ssPins[] = {SS_1_PIN, SS_2_PIN};
int servoPins[] = {SERVO_1_PIN, SERVO_2_PIN};
int ledPins[] = {LED_1_PIN, LED_2_PIN};

// Khởi tạo đối tượng
MFRC522 mfrc522[NR_OF_READERS];
Servo servos[NR_OF_READERS];

// Biến trạng thái
bool isWaitingForResponse = false;    // Cờ báo đang chờ phản hồi từ Python
unsigned long responseTimer = 0;      // Biến đếm thời gian chờ
int currentReaderIndex = -1;          // Lưu chỉ số của đầu đọc vừa quét thẻ

void setup() {
  Serial.begin(9600);
  while (!Serial); // Chờ cổng Serial sẵn sàng (chỉ cần cho một số board)
  
  SPI.begin(); // Khởi tạo bus SPI

  Serial.println(F("\n[INFO] Khoi dong he thong kiem soat ra vao..."));

  // Khởi tạo các đầu đọc RFID
  for (uint8_t i = 0; i < NR_OF_READERS; i++) {
    mfrc522[i].PCD_Init(ssPins[i], RST_PIN);
    Serial.print(F("[INFO] Dau doc #"));
    Serial.print(i + 1);
    
    // =======================================================
    // PHẦN ĐƯỢC SỬA LỖI NẰM Ở ĐÂY
    // =======================================================
    // In thông tin firmware của chip ra Serial Monitor để debug
    mfrc522[i].PCD_DumpVersionToSerial();
    
    // Thực hiện tự kiểm tra (self-test) để chắc chắn module hoạt động
    if (mfrc522[i].PCD_PerformSelfTest()) {
      Serial.println(F(" -> Status: OK"));
    } else {
      Serial.println(F(" -> Status: LOI! Vui long kiem tra lai ket noi."));
    }
    // =======================================================
  }

  // Khởi tạo các servo và LED
  for (uint8_t i = 0; i < NR_OF_READERS; i++) {
    servos[i].attach(servoPins[i]);
    servos[i].write(SERVO_CLOSE_ANGLE); // Đảm bảo cửa đóng khi khởi động
    
    pinMode(ledPins[i], OUTPUT);
    digitalWrite(ledPins[i], LOW); // Tắt LED
  }

  // Khởi tạo còi buzzer
  pinMode(BUZZER_PIN, OUTPUT);
  
  Serial.println(F("[INFO] He thong san sang. Dang cho quet the..."));
  Serial.println(F("-------------------------------------------------"));
}

void loop() {
  // 1. Kiểm tra phản hồi từ Python nếu có
  handlePythonResponse();
  
  // 2. Kiểm tra timeout nếu đang chờ phản hồi
  checkResponseTimeout();
  
  // 3. Nếu không đang chờ, quét thẻ từ các đầu đọc
  if (!isWaitingForResponse) {
    scanForCards();
  }
}

/**
 * @brief Quét thẻ từ lần lượt các đầu đọc.
 */
void scanForCards() {
  for (uint8_t i = 0; i < NR_OF_READERS; i++) {
    // Tìm thẻ mới và đọc UID
    if (mfrc522[i].PICC_IsNewCardPresent() && mfrc522[i].PICC_ReadCardSerial()) {
      
      // Chuyển UID sang dạng chuỗi String
      String uidString = getUIDString(mfrc522[i].uid.uidByte, mfrc522[i].uid.size);
      
      // Xác định hướng đi (VÀO/RA)
      String direction = (i == 0) ? "IN" : "OUT";
      
      // Gửi dữ liệu đến Python
      Serial.print(direction);
      Serial.print(":UID:");
      Serial.println(uidString);
      
      // Đặt trạng thái chờ phản hồi
      isWaitingForResponse = true;
      responseTimer = millis();
      currentReaderIndex = i;
      
      // Dừng đọc thẻ hiện tại
      mfrc522[i].PICC_HaltA();
      mfrc522[i].PCD_StopCrypto1();
      
      return; // Thoát vòng lặp để chờ phản hồi, tránh quét liên tục
    }
  }
}

/**
 * @brief Lắng nghe và xử lý phản hồi từ Python qua cổng Serial.
 */
void handlePythonResponse() {
  if (Serial.available() > 0) {
    String response = Serial.readStringUntil('\n');
    response.trim();
    
    // Chỉ xử lý nếu đang thực sự chờ phản hồi
    if (isWaitingForResponse && currentReaderIndex != -1) {
      if (response == "ALLOW") {
        grantAccess(currentReaderIndex);
      } else { // DENY hoặc phản hồi không hợp lệ
        denyAccess(currentReaderIndex);
      }
      
      // Reset trạng thái chờ
      isWaitingForResponse = false;
      currentReaderIndex = -1;
    }
  }
}

/**
 * @brief Kiểm tra nếu thời gian chờ phản hồi vượt quá giới hạn.
 */
void checkResponseTimeout() {
  if (isWaitingForResponse && (millis() - responseTimer > RESPONSE_TIMEOUT)) {
    Serial.println(F("[ERROR] Timeout! Khong nhan duoc phan hoi tu Python."));
    denyAccess(currentReaderIndex); // Từ chối truy cập nếu timeout
    
    // Reset trạng thái chờ
    isWaitingForResponse = false;
    currentReaderIndex = -1;
  }
}

/**
 * @brief Thực thi khi truy cập được cho phép.
 * @param readerIndex Chỉ số của đầu đọc (0 hoặc 1).
 */
void grantAccess(uint8_t readerIndex) {
  Serial.print(F("[ACCESS] CHO PHEP - Mo cua #"));
  Serial.println(readerIndex + 1);
  
  // Bật LED xanh và kêu bíp thành công
  digitalWrite(ledPins[readerIndex], HIGH);
  beep(true);
  
  // Mở cửa
  servos[readerIndex].write(SERVO_OPEN_ANGLE);
  
  // Giữ cửa mở
  delay(DOOR_OPEN_TIME);
  
  // Đóng cửa và tắt LED
  servos[readerIndex].write(SERVO_CLOSE_ANGLE);
  digitalWrite(ledPins[readerIndex], LOW);
  
  Serial.print(F("[INFO] Da dong cua #"));
  Serial.println(readerIndex + 1);
  Serial.println(F("-------------------------------------------------"));
}

/**
 * @brief Thực thi khi truy cập bị từ chối.
 * @param readerIndex Chỉ số của đầu đọc (0 hoặc 1).
 */
void denyAccess(uint8_t readerIndex) {
  Serial.print(F("[ACCESS] TU CHOI - Cua #"));
  Serial.println(readerIndex + 1);
  
  // Nháy LED đỏ 3 lần và kêu bíp thất bại
  for (int i = 0; i < 3; i++) {
    digitalWrite(ledPins[readerIndex], HIGH);
    delay(150);
    digitalWrite(ledPins[readerIndex], LOW);
    delay(150);
  }
  beep(false);
  Serial.println(F("-------------------------------------------------"));
}


// --- CÁC HÀM TIỆN ÍCH ---

/**
 * @brief Chuyển UID từ dạng byte array sang String để dễ xử lý.
 * @param buffer Mảng byte chứa UID.
 * @param bufferSize Kích thước của mảng byte.
 * @return Chuỗi String chứa UID đã được định dạng.
 */
String getUIDString(byte *buffer, byte bufferSize) {
  String uid = "";
  for (byte i = 0; i < bufferSize; i++) {
    if (buffer[i] < 0x10) {
      uid += "0"; // Thêm số 0 ở đầu cho các byte < 16
    }
    uid += String(buffer[i], HEX);
  }
  uid.toUpperCase();
  return uid;
}

/**
 * @brief Phát ra âm thanh từ còi buzzer.
 * @param isSuccess True nếu là âm thanh thành công, False nếu là thất bại.
 */
void beep(bool isSuccess) {
  if (isSuccess) {
    tone(BUZZER_PIN, 1200, 200); // Tiếng bíp cao, ngắn
  } else {
    tone(BUZZER_PIN, 300, 500); // Tiếng bíp trầm, dài
  }
}