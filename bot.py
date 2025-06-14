import os
import re
import discord
import yt_dlp
import aiohttp
import isodate
import asyncio
import shutil
import random
from discord.ext import commands
from discord.ui import Button, View, Select
from dotenv import load_dotenv
import logging
from cachetools import TTLCache

# -----------------------------#
#    Đọc Thông Tin Proxy        #
# -----------------------------#

# Lấy đường dẫn tuyệt đối của thư mục hiện tại
current_dir = os.path.dirname(os.path.abspath(__file__))

# Đường dẫn đến file proxy.txt
proxy_txt_path = os.path.join(current_dir, "proxy.txt")

# Kiểm tra xem proxy.txt có tồn tại không
if not os.path.isfile(proxy_txt_path):
    logging.warning(f"Không tìm thấy {proxy_txt_path}. Bot sẽ chạy mà không sử dụng proxy.")
    PROXY_URL = None
else:
    # Đọc nội dung proxy từ proxy.txt
    with open(proxy_txt_path, 'r') as proxy_file:
        PROXY_URL = proxy_file.read().strip()

    if not PROXY_URL:
        logging.info("Không sử dụng proxy vì proxy.txt trống.")
        PROXY_URL = None

# Đường dẫn đến file cookies.txt
cookies_txt_path = os.path.join(current_dir, "cookies.txt")

# Kiểm tra xem cookies.txt có tồn tại và có nội dung không
if not os.path.isfile(cookies_txt_path):
    logging.warning(f"Không tìm thấy {cookies_txt_path}. Bot sẽ chạy mà không sử dụng cookies.")
    COOKIES_PATH = None
else:
    # Kiểm tra nếu file cookies có nội dung
    with open(cookies_txt_path, 'r', encoding='utf-8') as f:
        cookies_content = f.read().strip()
    
    if not cookies_content or cookies_content == "# Netscape HTTP Cookie File":
        logging.warning(f"File cookies.txt trống hoặc chỉ có header. Bot sẽ chạy mà không sử dụng cookies.")
        COOKIES_PATH = None
    else:
        COOKIES_PATH = cookies_txt_path
        logging.info(f"Sử dụng cookies từ {COOKIES_PATH}")



# -----------------------------#
#        Cài Đặt Logging        #
# -----------------------------#

# Thiết lập logging để theo dõi và gỡ lỗi
logging.basicConfig(
    level=logging.INFO,  # Thiết lập mức logging (INFO, DEBUG, ERROR, v.v.)
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',  # Định dạng log
    handlers=[
        logging.FileHandler("bot.log"),  # Ghi log vào file
        logging.StreamHandler()          # Ghi log ra console
    ]
)
logger = logging.getLogger(__name__)

# -----------------------------#
#        Cài Đặt Môi Trường     #
# -----------------------------#

# Tải biến môi trường từ file .env
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')          # Token Discord Bot
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')  # API Key YouTube

# Đường dẫn đến ffmpeg trên hệ thống Ubuntu (sử dụng 'ffmpeg' từ PATH)
FFMPEG_PATH = 'ffmpeg'  # Hoặc sử dụng '/usr/bin/ffmpeg' nếu cần thiết

# Kiểm tra xem ffmpeg có tồn tại không
if not shutil.which(FFMPEG_PATH):
    raise FileNotFoundError(
        f"FFmpeg executable không tìm thấy. Vui lòng đảm bảo rằng ffmpeg đã được cài đặt và thêm vào PATH."
    )

# -----------------------------#
#        Định Nghĩa Intents     #
# -----------------------------#

# Định Nghĩa các intents trước khi khởi tạo bot
intents = discord.Intents.default()
intents.message_content = True       # Cho phép bot đọc nội dung tin nhắn
intents.guilds = True                # Cho phép bot nhận sự kiện liên quan đến guilds (máy chủ)
intents.voice_states = True          # Cho phép bot nhận sự kiện liên quan đến trạng thái giọng nói

# -----------------------------#
#        Định Nghĩa Hàm Hỗ Trợ #
# -----------------------------#

def parse_duration(duration_iso8601):
    """
    Phân tích duration từ ISO 8601 sang định dạng HH:MM:SS hoặc MM:SS.
    """
    try:
        duration = isodate.parse_duration(duration_iso8601)
        total_seconds = int(duration.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}:{minutes:02}:{seconds:02}"
        else:
            return f"{minutes}:{seconds:02}"
    except Exception as e:
        logger.error(f"Lỗi khi phân tích duration: {e}")
        return "Unknown"

def generate_queue_list(music_queue):
    """
    Tạo danh sách các bài hát trong hàng đợi dưới dạng chuỗi.
    """
    if music_queue.empty():
        return "Hàng đợi trống."
    queue_list = ""
    for idx, song in enumerate(music_queue._queue, start=1):
        queue_list += f"{idx}. {song['title']} - {song['duration']}\n"
    return queue_list

def truncate_label(text, max_length):
    """
    Rút gọn văn bản nếu vượt quá độ dài tối đa.
    """
    if len(text) > max_length:
        return text[:max_length-3] + '...'
    return text

# Định Nghĩa Hàm is_url trước khi sử dụng trong lệnh play
URL_REGEX = re.compile(
    r'^(https?://)?(www\.)?(youtube\.com|youtu\.?be)/.+$'
)

def is_url(query):
    """
    Kiểm tra xem chuỗi có phải là URL của YouTube không.
    """
    return re.match(URL_REGEX, query) is not None

# -----------------------------#
#        Định Nghĩa MusicPlayer#
# -----------------------------#

class MusicPlayer:
    """
    Lớp quản lý phát nhạc cho mỗi guild.
    """    
    def __init__(self, guild_id, text_channel):
        self.guild_id = guild_id
        self.voice_client = None
        self.voice_channel = None  # Kênh thoại mà bot đang kết nối
        self.current_song = None
        self.is_paused = False
        self.is_looping = False
        self.music_queue = asyncio.Queue()
        self.current_control_message = None
        self.disconnect_task = None
        self.audio_cache = TTLCache(maxsize=100, ttl=3600)  # Bộ nhớ đệm với TTL 1 giờ
        self.text_channel = text_channel  # Kênh TextChannel để gửi thông báo
        self.played_songs = []  # Danh sách các bài hát đã được phát
        self.is_playing_from_cache = False  # Trạng thái đang phát từ bộ nhớ đệm

# -----------------------------#
#        Định Nghĩa YouTubeAPI  #
# -----------------------------#

class YouTubeAPI:
    """
    Lớp quản lý các yêu cầu tới YouTube API.
    """
    def __init__(self, api_key):
        self.api_key = api_key
        self.session = None

    async def init_session(self):
        """
        Khởi tạo session aiohttp.
        """
        self.session = aiohttp.ClientSession()

    async def close(self):
        """
        Đóng session aiohttp.
        """
        if self.session:
            await self.session.close()

    async def search_youtube(self, query, max_results=10):
        """
        Tìm kiếm video trên YouTube dựa trên truy vấn.
        """
        search_url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            'part': 'snippet',
            'q': query,
            'type': 'video',
            'maxResults': max_results,
            'key': self.api_key
        }
        try:
            async with self.session.get(search_url, params=params) as resp:
                if resp.status != 200:
                    logger.error(f"Error in YouTube search: {resp.status}")
                    return None
                data = await resp.json()
                video_ids = [item['id']['videoId'] for item in data.get('items', [])]

            if not video_ids:
                return []

            details_url = "https://www.googleapis.com/youtube/v3/videos"
            details_params = {
                'part': 'contentDetails',
                'id': ','.join(video_ids),
                'key': self.api_key
            }
            async with self.session.get(details_url, params=details_params) as details_resp:
                if details_resp.status != 200:
                    logger.error(f"Error in YouTube video details: {details_resp.status}")
                    return None
                details_data = await details_resp.json()
                id_to_duration = {}
                for item in details_data.get('items', []):
                    video_id = item['id']
                    duration_iso8601 = item['contentDetails']['duration']
                    duration = parse_duration(duration_iso8601)
                    id_to_duration[video_id] = duration

            results = []
            for item in data.get('items', []):
                video_id = item['id']['videoId']
                title = item['snippet']['title']
                thumbnail = item['snippet']['thumbnails']['default']['url']
                url = f"https://www.youtube.com/watch?v={video_id}"
                duration = id_to_duration.get(video_id, "Unknown")
                results.append({
                    'title': title,
                    'url': url,
                    'thumbnail': thumbnail,
                    'duration': duration
                })
            return results
        except Exception as e:
            logger.error(f"Lỗi khi tìm kiếm YouTube: {e}")
            return None

# -----------------------------#
#        Định Nghĩa Bot         #
# -----------------------------#

class MyBot(commands.Bot):
    """
    Lớp Bot kế thừa từ commands.Bot để quản lý các chức năng bot.
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.youtube_api = YouTubeAPI(YOUTUBE_API_KEY)
        self.music_players = {}  # Dictionary để quản lý MusicPlayer cho từng guild

    async def setup_hook(self):
        """
        Hook để khởi tạo các tài nguyên cần thiết khi bot đã sẵn sàng.
        """
        await self.youtube_api.init_session()

    async def close(self):
        """
        Đóng các tài nguyên khi bot tắt.
        """
        await self.youtube_api.close()
        await super().close()

# Instantiate the bot after defining classes
bot = MyBot(command_prefix='!', intents=intents)

# Helper function to get or create MusicPlayer for a guild
def get_music_player(guild_id, text_channel):
    """
    Lấy hoặc tạo MusicPlayer cho một guild cụ thể.
    """
    if guild_id not in bot.music_players:
        bot.music_players[guild_id] = MusicPlayer(guild_id, text_channel)
    return bot.music_players[guild_id]

# -----------------------------#
#    Định Nghĩa Các Lớp UI      #
# -----------------------------#

class MusicControlView(View):
    """
    Lớp View để quản lý các nút điều khiển nhạc (Pause, Resume, Skip, Loop).
    """
    def __init__(self, music_player):
        super().__init__(timeout=None)
        self.music_player = music_player

    @discord.ui.button(label="Tạm Dừng", style=discord.ButtonStyle.primary, emoji="⏸️")
    async def pause(self, interaction: discord.Interaction, button: Button):
        try:
            if self.music_player.voice_client and self.music_player.voice_client.is_playing():
                self.music_player.voice_client.pause()
                self.music_player.is_paused = True
                await interaction.response.send_message("⏸️ Nhạc đã tạm dừng!", ephemeral=True)
                await send_control_panel(self.music_player)
            else:
                await interaction.response.send_message("❗ Không có nhạc nào đang phát.", ephemeral=True)
        except Exception as e:
            logger.error(f"Lỗi trong nút Tạm Dừng: {e}")
            await interaction.response.send_message("❗ Đã xảy ra lỗi khi tạm dừng nhạc.", ephemeral=True)

    @discord.ui.button(label="Tiếp Tục", style=discord.ButtonStyle.success, emoji="▶️")
    async def resume(self, interaction: discord.Interaction, button: Button):
        try:
            if self.music_player.voice_client and self.music_player.is_paused:
                self.music_player.voice_client.resume()
                self.music_player.is_paused = False
                await interaction.response.send_message("▶️ Nhạc đã tiếp tục!", ephemeral=True)
                await send_control_panel(self.music_player)
            else:
                await interaction.response.send_message("❗ Không có nhạc nào đang tạm dừng.", ephemeral=True)
        except Exception as e:
            logger.error(f"Lỗi trong nút Tiếp Tục: {e}")
            await interaction.response.send_message("❗ Đã xảy ra lỗi khi tiếp tục nhạc.", ephemeral=True)

    @discord.ui.button(label="Bỏ Qua", style=discord.ButtonStyle.danger, emoji="⏭️")
    async def skip(self, interaction: discord.Interaction, button: Button):
        try:
            if self.music_player.voice_client and self.music_player.voice_client.is_playing():
                self.music_player.voice_client.stop()
                await interaction.response.send_message("⏭️ Đã bỏ qua bài hát!", ephemeral=True)
            else:
                await interaction.response.send_message("❗ Không có nhạc nào đang phát.", ephemeral=True)
        except Exception as e:
            logger.error(f"Lỗi trong nút Bỏ Qua: {e}")
            await interaction.response.send_message("❗ Đã xảy ra lỗi khi bỏ qua nhạc.", ephemeral=True)

    @discord.ui.button(label="Lặp Bài Hát", style=discord.ButtonStyle.secondary, emoji="🔁")
    async def loop(self, interaction: discord.Interaction, button: Button):
        try:
            self.music_player.is_looping = not self.music_player.is_looping
            state = "bật" if self.music_player.is_looping else "tắt"
            await interaction.response.send_message(f"🔁 Lặp bài hát đã {state}!", ephemeral=True)
            await send_control_panel(self.music_player)
        except Exception as e:
            logger.error(f"Lỗi trong nút Lặp Bài Hát: {e}")
            await interaction.response.send_message("❗ Đã xảy ra lỗi khi thay đổi chế độ lặp.", ephemeral=True)

class SongSelect(Select):
    """
    Lớp Select để người dùng chọn bài hát từ kết quả tìm kiếm.
    """
    def __init__(self, music_player, songs, user_voice_channel):
        options = [
            discord.SelectOption(
                label=truncate_label(f"{idx + 1}. {song['title']} - {song['duration']}", 100),
                value=str(idx)
            ) for idx, song in enumerate(songs)
        ]
        super().__init__(
            placeholder="Chọn bài hát bạn muốn phát...",
            min_values=1,
            max_values=1,
            options=options
        )
        self.music_player = music_player
        self.songs = songs
        self.user_voice_channel = user_voice_channel

    async def callback(self, interaction: discord.Interaction):
        try:
            selected_index = int(self.values[0])
            selected_song = self.songs[selected_index]
            await interaction.response.defer()
            await interaction.message.delete()
            await process_song_selection_from_selection(self.music_player, selected_song, self.user_voice_channel)
        except Exception as e:
            logger.error(f"Lỗi trong SongSelect callback: {e}")
            await interaction.response.send_message("❗ Đã xảy ra lỗi khi chọn bài hát.", ephemeral=True)

class SongSelectionView(View):
    """
    Lớp View chứa Select để chọn bài hát.
    """
    def __init__(self, music_player, songs, user_voice_channel):
        super().__init__(timeout=60)
        self.music_player = music_player
        self.songs = songs
        self.user_voice_channel = user_voice_channel
        self.add_item(SongSelect(music_player, songs, user_voice_channel))

    async def on_timeout(self):
        try:
            await self.message.edit(content="⏰ Thời gian chọn bài hát đã hết.", view=None)
        except Exception as e:
            logger.error(f"Lỗi khi timeout SongSelectionView: {e}")

    async def send(self, message):
        self.message = await message.edit(view=self)

# -----------------------------#
#      Định Nghĩa Các Hàm       #
# -----------------------------#

async def send_control_panel(music_player):
    """
    Gửi hoặc cập nhật bảng điều khiển nhạc (embed và view).
    """    
    channel = music_player.text_channel
    # Xóa bảng điều khiển cũ (nếu có)
    if music_player.current_control_message:
        try:
            await music_player.current_control_message.delete()
        except discord.NotFound:
            pass
        music_player.current_control_message = None
    if music_player.current_song:
        if music_player.is_playing_from_cache:
            status_message = f"🎵 Phát từ bộ nhớ: **{music_player.current_song['title']}** ({music_player.current_song['duration']})"
        else:
            status_message = f"🎵 Đang phát: **{music_player.current_song['title']}** ({music_player.current_song['duration']})"
    else:
        status_message = "🎵 Không có bài hát nào đang được phát."
    
    # Set embed color based on playback status
    embed_color = discord.Color.blue() if music_player.is_playing_from_cache else (
        discord.Color.green() if not music_player.is_paused else discord.Color.gold()
    )
    
    embed = discord.Embed(
        title="🎶 Music Player",
        description=status_message,
        color=embed_color
    )
    embed.add_field(        
        name="🎧 Trạng thái phát nhạc",
        value="🔄 Đang phát lại các bài hát trước đó" if music_player.is_playing_from_cache else ("▶️ Đang phát" if not music_player.is_paused else "⏸️ Đã tạm dừng")
    )
    embed.add_field(
        name="🔄 Lặp",
        value="Bật" if music_player.is_looping else "Tắt"
    )
    embed.add_field(
        name="📋 Hàng đợi",
        value=generate_queue_list(music_player.music_queue),
        inline=False
    )    # Custom footer based on playback state
    if music_player.is_playing_from_cache:
        footer_text = "🔄 Đang Phát lại các bài hát trước đó | Điều khiển bằng các nút bên dưới"
    else:
        footer_text = "Điều khiển nhạc bằng các nút bên dưới!"
    
    embed.set_footer(text=footer_text)

    if music_player.current_song and music_player.current_song['thumbnail']:
        embed.set_thumbnail(url=music_player.current_song['thumbnail'])

    view = MusicControlView(music_player)
    music_player.current_control_message = await channel.send(embed=embed, view=view)
    await update_bot_status(music_player)

async def update_bot_status(music_player):
    """
    Cập nhật trạng thái của bot dựa trên trạng thái nhạc hiện tại.
    """
    if music_player.current_song:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.playing,
                name=f": {music_player.current_song['title']}"
            )
        )
    elif not music_player.music_queue.empty():
        next_song = music_player.music_queue._queue[0]
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.playing,
                name=f"!play {next_song['title']}"
            )
        )
    else:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="!play + Tên Bài Hát"
            )
        )

async def get_audio_stream_url(music_player, url):
    """
    Lấy URL luồng âm thanh từ cache hoặc YouTube.
    """
    if url in music_player.audio_cache:
        logger.info(f"Lấy URL âm thanh từ bộ nhớ đệm cho guild {music_player.guild_id}.")
        return music_player.audio_cache[url]
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'noplaylist': True,
        'default_search': 'auto',
        'nocheckcertificate': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'restrictfilenames': True,
        'skip_download': True,
        'cachedir': False,
        'extractor_args': {
            'youtube': {
                'skip': ['hls', 'dash'],
                'player_skip': ['configs', 'webpage'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip,deflate',
            'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.7',
            'Connection': 'close'
        }
    }

    if PROXY_URL:
        ydl_opts['proxy'] = PROXY_URL

    # Thêm hỗ trợ cookies nếu có
    if COOKIES_PATH:
        ydl_opts['cookiefile'] = COOKIES_PATH
        logger.info(f"Sử dụng cookies từ {COOKIES_PATH}")

    # Thử nhiều lần với các cấu hình khác nhau
    retry_configs = [
        {},  # Cấu hình mặc định
        {'extractor_args': {'youtube': {'skip': ['dash']}}},  # Bỏ qua DASH
        {'format': 'worst'},  # Chất lượng thấp nhất
    ]

    for attempt, extra_opts in enumerate(retry_configs, 1):
        try:
            current_opts = {**ydl_opts, **extra_opts}
            logger.info(f"Thử lấy URL âm thanh lần {attempt} cho guild {music_player.guild_id}")
            
            with yt_dlp.YoutubeDL(current_opts) as ydl:
                # Sử dụng asyncio.to_thread để chạy hàm đồng bộ trong một thread
                info = await asyncio.to_thread(ydl.extract_info, url, download=False)
                if info is None:
                    logger.warning(f"yt_dlp trả về None cho thông tin video tại {url} (lần thử {attempt}).")
                    continue

                # Lấy luồng âm thanh trực tiếp
                audio_url = info.get('url')
                title = info.get('title', 'URL Provided')
                thumbnail = info.get('thumbnail')
                
                if not audio_url:
                    logger.warning(f"Không tìm thấy URL âm thanh trong lần thử {attempt}")
                    continue
                
                logger.info(f"Thành công lấy URL âm thanh cho guild {music_player.guild_id} ở lần thử {attempt}")
                
                # Lấy thời lượng từ info nếu có
                duration = "Unknown"
                if info.get('duration'):
                    duration_seconds = info.get('duration')
                    minutes, seconds = divmod(int(duration_seconds), 60)
                    hours, minutes = divmod(minutes, 60)
                    if hours > 0:
                        duration = f"{hours}:{minutes:02}:{seconds:02}"
                    else:
                        duration = f"{minutes}:{seconds:02}"
                
                # Lưu trữ vào bộ nhớ đệm dưới dạng dict
                music_player.audio_cache[url] = {
                    "url": audio_url,
                    "title": title,
                    "thumbnail": thumbnail,
                    "duration": duration
                }

                return music_player.audio_cache[url]
                
        except Exception as e:
            logger.warning(f"Lần thử {attempt} thất bại: {e}")
            if attempt == len(retry_configs):
                logger.error(f"Tất cả các lần thử đều thất bại cho URL {url}")
            continue
    
    # Nếu tất cả các lần thử đều thất bại
    logger.error(f"Không thể lấy audio stream URL tại {url} sau {len(retry_configs)} lần thử")
    return None

async def process_song_selection(ctx, song, user_voice_channel):
    """
    Xử lý bài hát được chọn từ lệnh play.
    """
    music_player = get_music_player(ctx.guild.id, ctx.channel)
    await process_song_selection_from_selection(music_player, song, user_voice_channel)

async def process_song_selection_from_selection(music_player, song, user_voice_channel):
    """
    Xử lý bài hát được chọn từ giao diện chọn bài hát.
    """
    try:
        logger.info(f"Đang xử lý bài hát: {song['title']} cho guild {music_player.guild_id}")

        # Hủy tác vụ ngắt kết nối nếu có
        if music_player.disconnect_task and not music_player.disconnect_task.cancelled():
            music_player.disconnect_task.cancel()
            music_player.disconnect_task = None

        # Kiểm tra và kết nối vào kênh thoại nếu chưa kết nối
        if not music_player.voice_client:
            if not user_voice_channel:
                await music_player.text_channel.send("❗ Bạn cần vào một kênh thoại trước!")
                return
            try:
                music_player.voice_client = await user_voice_channel.connect()
                music_player.voice_channel = user_voice_channel
            except Exception as e:
                logger.error(f"Lỗi khi kết nối kênh thoại: {e}")
                await music_player.text_channel.send("❗ Không thể kết nối vào kênh thoại.")
                return
        elif music_player.voice_client.channel != user_voice_channel:
            try:
                await music_player.voice_client.move_to(user_voice_channel)
                music_player.voice_channel = user_voice_channel
            except Exception as e:
                logger.error(f"Lỗi khi di chuyển kênh thoại: {e}")
                await music_player.text_channel.send("❗ Không thể di chuyển vào kênh thoại.")
                return

        # Lấy URL luồng âm thanh
        audio_data = await get_audio_stream_url(music_player, song['url'])
        if not audio_data:
            await music_player.text_channel.send("❗ Không thể lấy luồng âm thanh của bài hát này.")
            return
        current_song_info = {
            "url": audio_data["url"],
            "title": audio_data["title"],
            "thumbnail": audio_data["thumbnail"],
            "duration": song['duration']
        }
        
        # Thêm bài hát đã phát vào danh sách đã phát
        music_player.played_songs.append(current_song_info)

        if music_player.voice_client.is_playing() or music_player.voice_client.is_paused():
            await music_player.music_queue.put(current_song_info)
            await send_control_panel(music_player)
        else:
            music_player.current_song = current_song_info
            music_player.is_playing_from_cache = False  # Đánh dấu không phát từ cache
            try:
                logger.info(f"Đang cố gắng phát: {current_song_info['title']} cho guild {music_player.guild_id}")
                
                before_options = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
                if PROXY_URL:
                    before_options += f' -http_proxy {PROXY_URL}'

                music_player.voice_client.play(
                    discord.FFmpegOpusAudio(
                        executable=FFMPEG_PATH,
                        source=current_song_info['url'],
                        before_options=before_options,
                        options='-vn -c:a copy -loglevel quiet'  # Stream copy để giảm tải CPU
                    ),
                    after=lambda e: asyncio.run_coroutine_threadsafe(play_next(music_player.guild_id), bot.loop)
                )
                logger.info(f"Đã phát: {current_song_info['title']} cho guild {music_player.guild_id}")
                await send_control_panel(music_player)
            except Exception as e:
                logger.error(f"Lỗi khi phát nhạc: {e}")
                await music_player.text_channel.send("❗ Có lỗi xảy ra khi phát nhạc.")
    except Exception as e:
        logger.error(f"Lỗi trong process_song_selection_from_selection: {e}")
        await music_player.text_channel.send("❗ Đã xảy ra lỗi khi xử lý bài hát.")

async def play_next(guild_id):
    """
    Phát bài hát tiếp theo trong hàng đợi hoặc từ bộ nhớ đệm.
    """
    try:
        music_player = bot.music_players.get(guild_id)
        if not music_player:
            logger.error(f"Không tìm thấy MusicPlayer cho guild {guild_id}.")
            return

        channel = music_player.text_channel
        if not channel:
            logger.error(f"Không tìm thấy kênh text cho MusicPlayer của guild {guild_id}.")
            return

        if music_player.current_control_message:
            try:
                await music_player.current_control_message.delete()
            except discord.NotFound:
                pass
            music_player.current_control_message = None

        if music_player.is_looping and music_player.current_song:
            try:
                logger.info(f"Lặp lại bài hát: {music_player.current_song['title']} cho guild {guild_id}")

                before_options = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
                if PROXY_URL:
                    before_options += f' -http_proxy {PROXY_URL}'

                music_player.voice_client.play(
                    discord.FFmpegOpusAudio(
                        executable=FFMPEG_PATH,
                        source=music_player.current_song["url"],
                        before_options=before_options,
                        options='-vn -c:a copy -loglevel quiet'  # Stream copy để giảm tải CPU
                    ),
                    after=lambda e: asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)
                )
                logger.info(f"Đã phát lại: {music_player.current_song['title']} cho guild {guild_id}")
                await send_control_panel(music_player)
            except Exception as e:
                logger.error(f"Lỗi khi phát lại bài hát: {e}")
        elif not music_player.music_queue.empty():
            next_song = await music_player.music_queue.get()
            # Đảm bảo next_song là dict
            if isinstance(next_song, tuple):
                logger.error(f"Expected dict but got tuple in music_queue for guild {guild_id}.")
                next_song = {
                    "url": next_song[0],
                    "title": next_song[1],
                    "thumbnail": next_song[2],
                    "duration": "Unknown"
                }
            music_player.current_song = next_song
            try:
                logger.info(f"Đang phát bài tiếp theo: {next_song['title']} cho guild {guild_id}")

                before_options = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
                if PROXY_URL:
                    before_options += f' -http_proxy {PROXY_URL}'

                music_player.voice_client.play(
                    discord.FFmpegOpusAudio(
                        executable=FFMPEG_PATH,
                        source=next_song["url"],
                        before_options=before_options,
                        options='-vn -c:a copy -loglevel quiet'  # Stream copy để giảm tải CPU
                    ),
                    after=lambda e: asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)
                )                
                logger.info(f"Đã phát bài tiếp theo: {next_song['title']} cho guild {guild_id}")
                await send_control_panel(music_player)
            except Exception as e:
                logger.error(f"Lỗi khi phát bài tiếp theo: {e}")
        else:            # Hàng đợi trống, cố gắng nạp lại từ bộ nhớ đệm một bài hát
            cache_songs = list(music_player.audio_cache.values())
            if cache_songs:
                # Đánh dấu đang phát từ cache mà không spam thông báo
                music_player.is_playing_from_cache = True
                # Chọn một bài hát ngẫu nhiên từ cache
                song = random.choice(cache_songs)
                # Đảm bảo song là dict đầy đủ với tất cả các trường cần thiết
                if 'duration' not in song:
                    song['duration'] = "Unknown"
                await music_player.music_queue.put(song)  # Đảm bảo song là dict
                await play_next(guild_id)  # Gọi lại play_next để bắt đầu phát
            else:
                music_player.current_song = None
                music_player.is_playing_from_cache = False
                await channel.send("🎵 Hết hàng đợi và bộ nhớ đệm trống. Bot sẽ ngắt kết nối sau 15 phút nếu không có yêu cầu mới.")
                music_player.disconnect_task = asyncio.create_task(disconnect_after_delay(guild_id))
                await update_bot_status(music_player)
    except Exception as e:
        logger.error(f"Lỗi trong play_next cho guild {guild_id}: {e}")

async def disconnect_after_delay(guild_id):
    """
    Ngắt kết nối bot khỏi kênh thoại sau 15 phút không hoạt động.
    """
    try:
        await asyncio.sleep(900)  # 15 phút
        music_player = bot.music_players.get(guild_id)
        if not music_player:
            logger.error(f"Không tìm thấy MusicPlayer cho guild {guild_id} khi ngắt kết nối.")
            return

        channel = music_player.text_channel
        if not channel:
            logger.error(f"Không tìm thấy kênh text cho MusicPlayer của guild {guild_id} khi ngắt kết nối.")
            return

        if (music_player.voice_client and 
            not music_player.voice_client.is_playing() and 
            music_player.music_queue.empty()):
            await channel.send("🕒 15 phút đã trôi qua mà không có yêu cầu mới. Ngắt kết nối.")
            await music_player.voice_client.disconnect()
            music_player.voice_client = None
            music_player.voice_channel = None  # Reset voice_channel sau khi ngắt kết nối
            # music_player.text_channel = None  # Không reset text_channel để có thể tiếp tục sử dụng
            await update_bot_status(music_player)
    except asyncio.CancelledError:
        logger.info(f"Tác vụ ngắt kết nối đã bị hủy cho guild {guild_id}.")
    except Exception as e:
        logger.error(f"Lỗi khi ngắt kết nối sau thời gian chờ cho guild {guild_id}: {e}")

# -----------------------------#
#        Định Nghĩa Các Lệnh    #
# -----------------------------#

@bot.command(aliases=['p'])
async def play(ctx, *, query: str):
    """
    Lệnh để phát nhạc. Query có thể là tên bài hát hoặc URL YouTube.
    """
    try:
        user_voice = ctx.author.voice
        if not user_voice or not user_voice.channel:
            await ctx.send("❗ Bạn cần vào một kênh thoại trước!")
            return

        music_player = get_music_player(ctx.guild.id, ctx.channel)
        if is_url(query):            
            url = query
            audio_data = await get_audio_stream_url(music_player, url)
            if not audio_data:
                await ctx.send("❗ Không thể lấy luồng âm thanh của URL này.")
                return
            await process_song_selection(ctx, {
                'url': url,
                'title': audio_data["title"],
                'thumbnail': audio_data["thumbnail"],
                'duration': audio_data["duration"]  # Sử dụng thời lượng từ audio_data thay vì "Unknown"
            }, user_voice.channel)
        else:
            await ctx.send(f"🔍 Đang tìm kiếm **{query}** trên YouTube...")
            search_results = await bot.youtube_api.search_youtube(query)

            if not search_results:
                await ctx.send("❌ Không tìm thấy kết quả nào cho tìm kiếm của bạn.")
                return

            embed = discord.Embed(
                title="Kết Quả Tìm Kiếm",
                description=f"Tìm thấy {len(search_results)} kết quả cho **{query}**:",
                color=discord.Color.blue()
            )
            for idx, song in enumerate(search_results, start=1):
                embed.add_field(
                    name=f"{idx}. {song['title']} - {song['duration']}",
                    value="",
                    inline=False
                )

            view = SongSelectionView(music_player, search_results, user_voice.channel)
            message = await ctx.send(embed=embed, view=view)
            await view.send(message)
    except Exception as e:
        logger.error(f"Lỗi trong lệnh play: {e}")
        await ctx.send("❗ Đã xảy ra lỗi khi xử lý lệnh play.")

@bot.command()
async def stop(ctx):
    """
    Lệnh để dừng phát nhạc và ngắt kết nối bot khỏi kênh thoại.
    """
    try:
        music_player = bot.music_players.get(ctx.guild.id)
        if not music_player:
            await ctx.send("❗ Bot không kết nối vào kênh thoại nào.")
            return

        if music_player.disconnect_task and not music_player.disconnect_task.cancelled():
            music_player.disconnect_task.cancel()
            music_player.disconnect_task = None

        # Xóa hàng đợi một cách an toàn
        while not music_player.music_queue.empty():
            try:
                music_player.music_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        music_player.current_song = None
        if music_player.voice_client.is_playing() or music_player.voice_client.is_paused():
            music_player.voice_client.stop()

        if music_player.current_control_message:
            try:
                await music_player.current_control_message.delete()
            except Exception as e:
                logger.error(f"Lỗi khi xóa control message: {e}")
            music_player.current_control_message = None

        await music_player.voice_client.disconnect()
        music_player.voice_client = None
        music_player.voice_channel = None  # Reset voice_channel sau khi ngắt kết nối
        # music_player.text_channel = None  # Không reset text_channel để có thể sử dụng lại
        await ctx.send("🛑 Bot đã ngắt kết nối và xóa hàng đợi.")
        await update_bot_status(music_player)
    except Exception as e:
        logger.error(f"Lỗi trong lệnh stop: {e}")
        await ctx.send("❗ Đã xảy ra lỗi khi ngắt kết nối khỏi kênh thoại.")

# -----------------------------#
#        Định Nghĩa Sự Kiện     #
# -----------------------------#

@bot.event
async def on_ready():
    """
    Sự kiện khi bot đã sẵn sàng và đăng nhập thành công.
    """
    if PROXY_URL:
        logger.info(f"Bot đang sử dụng proxy: {PROXY_URL}")
    else:
        logger.info("Bot không sử dụng proxy.")
    logger.info(f'Bot đã đăng nhập với tên: {bot.user}')

@bot.event
async def on_disconnect():
    """
    Sự kiện khi bot ngắt kết nối khỏi Discord.
    """
    logger.info("Bot đã ngắt kết nối khỏi Discord.")

@bot.event
async def on_error(event, *args, **kwargs):
    """
    Sự kiện xử lý lỗi trong các sự kiện Discord.
    """
    logger.exception(f"Lỗi xảy ra trong event {event}.")

@bot.event
async def on_command_error(ctx, error):
    """
    Handler lỗi toàn cục cho các lệnh Discord.
    """
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("❗ Lệnh không tồn tại. Vui lòng kiểm tra lại.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❗ Thiếu đối số cần thiết cho lệnh này.")
    else:
        logger.error(f"Lỗi trong lệnh {ctx.command}: {error}")
        await ctx.send("❗ Đã xảy ra lỗi khi xử lý lệnh của bạn.")

# -----------------------------#
#        Chạy Bot               #
# -----------------------------#

# Đảm bảo đóng session aiohttp khi bot tắt bằng cách sử dụng phương thức close của lớp MyBot
# Không cần tạo task ở đây

bot.run(TOKEN)
