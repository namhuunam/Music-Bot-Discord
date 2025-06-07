# Hướng dẫn xuất Cookies từ YouTube cho Discord Music Bot

## Bước 1: Cài đặt Extension
1. Mở Chrome/Firefox và đăng nhập vào YouTube
2. Cài đặt extension "Get cookies.txt LOCALLY" hoặc "cookies.txt"
   - Chrome: https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc
   - Firefox: https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/

## Bước 2: Xuất Cookies
1. Vào youtube.com và đảm bảo bạn đã đăng nhập
2. Click vào icon extension
3. Chọn "Export cookies for this site" hoặc "Export for youtube.com"
4. Copy nội dung được xuất

## Bước 3: Lưu Cookies
1. Mở file `cookies.txt` trong thư mục bot
2. Xóa toàn bộ nội dung hướng dẫn hiện tại
3. Dán cookies đã copy vào file
4. Lưu file

## Bước 4: Khởi động lại Bot
- Bot sẽ tự động phát hiện và sử dụng cookies từ file `cookies.txt`
- Trong log sẽ hiển thị: "Sử dụng cookies từ [đường dẫn]"

## Lưu ý:
- Cookies có thời hạn, khi hết hạn cần xuất lại
- Không chia sẻ file cookies với người khác
- Nếu không muốn dùng cookies, để file cookies.txt trống hoặc xóa file

## Kiểm tra:
- Nếu thành công, bot sẽ không còn báo lỗi "Sign in to confirm you're not a bot"
- Có thể phát được các video YouTube bình thường
