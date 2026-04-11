import httpx
from PIL import Image as PILImage, ImageDraw, ImageFont
from datetime import datetime, timedelta
import os, re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *

@register("asoul_calendar", "awslYui", "枝江直播日程", "1.0")
class CalendarPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.url = "https://asoul.love/calendar.ics"
        self.image_path = "data/asoul_schedule.png"
        self.font_path = os.path.join(os.path.dirname(__file__), "msyh.ttf")
        self.context.register_task("0 */12 * * *", self.update_calendar_image, desc="A-SOUL 日程表定时更新")

    def parse_ics_advanced(self, text):
        """增强版解析：支持直播间链接识别和状态识别"""
        events = []
        items = re.findall(r"BEGIN:VEVENT.*?END:VEVENT", text, re.S)
        for item in items:
            summary = re.search(r"SUMMARY:(.*)", item).group(1).strip() if re.search(r"SUMMARY:(.*)", item) else "未知直播"
            dtstart = re.search(r"DTSTART:(.*)", item).group(1).strip() if re.search(r"DTSTART:(.*)", item) else ""
            location = re.search(r"LOCATION:(.*)", item).group(1).strip() if re.search(r"LOCATION:(.*)", item) else ""
            
            if not dtstart: continue
            
            # 状态识别
            is_canceled = any(kw in summary for kw in ["取消", "延期", "更改"])
            
            try:
                t_fmt = "%Y%m%dT%H%M%SZ" if 'Z' in dtstart else "%Y%m%dT%H%M%S"
                utc_dt = datetime.strptime(dtstart[:16].replace('Z',''), t_fmt[:8]+'T'+t_fmt[9:15])
                bj_dt = utc_dt + timedelta(hours=8)
                events.append({
                    "time": bj_dt,
                    "title": summary,
                    "url": location,
                    "canceled": is_canceled
                })
            except: continue
        return sorted(events, key=lambda x: x["time"])

    def get_color_by_url(self, url, title):
        """精准识别直播间颜色"""
        mapping = {
            "22637261": "#E799B0", # 嘉然
            "22625027": "#576690", # 乃琳
            "22632424": "#DB7D74", # 贝拉
            "30849777": "#C93773", # 心宜
            "30858592": "#7252C0"  # 思诺
        }
        for uid, color in mapping.items():
            if uid in url: return color
        
        # 兜底：如果 URL 没识别到，按标题关键词再猜一次
        colors_kw = {"嘉然": "#E799B0", "贝拉": "#DB7D74", "乃琳": "#576690", "思诺": "#7252C0", "心宜": "#C93773"}
        for k, v in colors_kw.items():
            if k in title: return v
        return "#A1A7B3" # 团播或未识别

    async def update_calendar_image(self):
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(self.url, timeout=10)
                all_events = self.parse_ics_advanced(resp.text)
            except: return False
            
            # 获取本周一的日期
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start_of_week = today - timedelta(days=today.weekday())
            
            # 按天归类日程 (0=周一, 6=周日)
            week_data = {i: [] for i in range(7)}
            for ev in all_events:
                diff = (ev["time"].date() - start_of_week.date()).days
                if 0 <= diff <= 6:
                    week_data[diff].append(ev)

            # 画布参数
            COL_WIDTH = 240
            TOP_HEIGHT = 120
            CANVAS_WIDTH = COL_WIDTH * 7 + 100
            CANVAS_HEIGHT = 1000 # 预设高度
            
            img = PILImage.new('RGB', (CANVAS_WIDTH, CANVAS_HEIGHT), color="#F4F5F7")
            draw = ImageDraw.Draw(img)
            
            try:
                f_title = ImageFont.truetype(self.font_path, 40)
                f_date = ImageFont.truetype(self.font_path, 22)
                f_time = ImageFont.truetype(self.font_path, 20)
                f_main = ImageFont.truetype(self.font_path, 18)
            except: return False

            draw.text((CANVAS_WIDTH//2 - 80, 40), "本 周 日 程", fill="#222222", font=f_title)

            week_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            
            for day_idx in range(7):
                curr_date = start_of_week + timedelta(days=day_idx)
                x_base = 50 + day_idx * COL_WIDTH
                
                # 画日期头
                date_str = curr_date.strftime('%Y/%m/%d') + f" {week_names[day_idx]}"
                draw.text((x_base, TOP_HEIGHT), date_str, fill="#666666", font=f_date)
                
                y_offset = TOP_HEIGHT + 50
                for ev in week_data[day_idx]:
                    # 颜色处理
                    main_color = "#D0D3D9" if ev["canceled"] else self.get_color_by_url(ev["url"], ev["title"])
                    
                    # 时间
                    draw.text((x_base, y_offset), ev["time"].strftime('%H:%M'), fill="#666666", font=f_time)
                    y_offset += 30
                    
                    # 绘制日程卡片
                    rect = [x_base, y_offset, x_base + COL_WIDTH - 30, y_offset + 90]
                    draw.rounded_rectangle(rect, radius=10, fill=main_color)
                    
                    # 文字 (处理自动换行简易版)
                    display_title = ev["title"][:20] # 截断防止溢出
                    text_color = "#FFFFFF"
                    draw.text((x_base + 15, y_offset + 15), display_title, fill=text_color, font=f_main)
                    
                    # 如果取消，加删除线
                    if ev["canceled"]:
                        line_y = y_offset + 45
                        draw.line([x_base + 10, line_y, x_base + COL_WIDTH - 40, line_y], fill="#444444", width=2)
                    
                    y_offset += 120
            
            # 保存前裁剪一下高度（此处简化，实际可根据 y_offset 动态裁剪）
            os.makedirs(os.path.dirname(self.image_path), exist_ok=True)
            img.save(self.image_path)
            return True
