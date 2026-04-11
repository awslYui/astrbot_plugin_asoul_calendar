import arrow
import httpx
from ics import Calendar
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timedelta
import os
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *

@register("asoul_calendar", "A-SOUL 直播日程排版插件", "1.1.0")
class CalendarPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.url = "https://asoul.love/calendar.ics"
        self.image_path = "data/asoul_schedule.png"
        
        # 字体文件路径 (必须是支持中文的 .ttf 或 .ttc 文件)
      
        self.font_path = "msyh.ttf" 
        
        # 注册定时任务：每 12 小时更新一次
        @self.context.register_task("0 */12 * * *")
        async def scheduled_update():
            await self.update_calendar_image()

    def get_member_color(self, event_name: str) -> str:
        """根据日程标题包含的成员名字，返回对应的背景颜色"""
        if "嘉然" in event_name:
            return "#E799B0"
        elif "贝拉" in event_name:
            return "#DB7D74"
        elif "乃琳" in event_name:
            return "#576690"
        elif "思诺" in event_name:
            return "#7252C0"
        elif "心宜" in event_name:
            return "#C93773"
        else:
            return "#555555" # 如果是团播或未指明，使用低调的深灰色

    async def update_calendar_image(self):
        """下载 ics 并生成类似 B 站动态排版的图片"""
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(self.url)
                if resp.status_code != 200:
                    return False
            except Exception:
                return False
            
            calendar = Calendar(resp.text)
            
            # 筛选未来 7 天的日程
            now = datetime.now()
            upcoming_events = []
            for event in sorted(calendar.events):
                # 将 UTC 时间转换为北京时间用于比较
                bj_time = event.begin.to('Asia/Shanghai')
                if now <= bj_time.datetime.replace(tzinfo=None) <= now + timedelta(days=7):
                    upcoming_events.append((bj_time, event.name))
            
            # 如果没有日程，生成一张默认图
            if not upcoming_events:
                upcoming_events.append((datetime.now(), "本周暂无已公开的直播安排"))

            # B 站动态风格 UI 设置
            card_width = 700
            card_height = 90
            padding = 20
            margin_top = 100
            
            # 动态计算整个图片的画布高度
            img_height = margin_top + len(upcoming_events) * (card_height + padding) + 50
            img_width = card_width + 80
            
            # 创建画布：B 站深色模式背景 #1C1C1E 或浅色模式的温和底色
            # 这里用一个非常淡的灰色作为底板，让彩色的卡片更突出
            img = Image.new('RGB', (img_width, img_height), color="#F4F5F7")
            draw = ImageDraw.Draw(img)
            
            # 加载字体
            try:
                title_font = ImageFont.truetype(self.font_path, 36)
                main_font = ImageFont.truetype(self.font_path, 28)
                time_font = ImageFont.truetype(self.font_path, 24)
            except IOError:
                # 如果找不到字体，使用默认字体 (警告：默认字体无法显示中文)
                title_font = ImageFont.load_default()
                main_font = title_font
                time_font = title_font

            # 绘制大标题
            draw.text((40, 30), "A-SOUL 本周直播日程", fill="#222222", font=title_font)
            
            y_offset = margin_top
            for bj_time, event_name in upcoming_events:
                # 提取格式化时间，例如：04月15日 20:00
                time_str = bj_time.format('MM月DD日 HH:mm')
                if "暂无" in event_name:
                    time_str = "提示"
                
                # 获取该日程的背景色
                bg_color = self.get_member_color(event_name)
                
                # 绘制日程卡片 (带有圆角的矩形)
                x0, y0 = 40, y_offset
                x1, y1 = 40 + card_width, y_offset + card_height
                draw.rounded_rectangle([x0, y0, x1, y1], radius=15, fill=bg_color)
                
                # 在色块内绘制文字：全部要求为白色
                # 时间绘制在左侧
                draw.text((x0 + 25, y0 + 30), time_str, fill="#FFFFFF", font=time_font)
                
                # 内容标题绘制在右侧一点的位置，用一条简单的竖线分割视觉
                draw.text((x0 + 220, y0 + 25), f"|  {event_name}", fill="#FFFFFF", font=main_font)
                
                y_offset += card_height + padding
            
            # 确保 data 目录存在
            os.makedirs(os.path.dirname(self.image_path), exist_ok=True)
            img.save(self.image_path)
            return True

    @filter.command("日程表")
    async def send_calendar(self, event: AstrMessageEvent):
        """指令 1: 发送现有的日程图片"""
        if os.path.exists(self.image_path):
            yield event.image_result(self.image_path)
        else:
            yield event.plain_result("暂无日程图片，请执行 /更新日程表 同步最新数据哦。")

    @filter.command("更新日程表")
    async def force_update(self, event: AstrMessageEvent):
        """指令 2: 立即更新并发送"""
        yield event.plain_result("正在拉取最新日历并努力排版中，请稍候...")
        success = await self.update_calendar_image()
        if success:
            yield event.image_result(self.image_path)
        else:
            yield event.plain_result("更新失败了，请检查网络或日历源是否正常。")
