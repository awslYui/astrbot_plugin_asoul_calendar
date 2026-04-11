import httpx
from PIL import Image as PILImage, ImageDraw, ImageFont, ImageChops
from datetime import datetime, timedelta
import os, re
import arrow # ics 自带 arrow，用于稳定处理时间
from ics import Calendar
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.STAR import Context, STAR, register
from astrbot.api.all import *

@register("asoul_calendar", "awslYui", "A-SOUL 直播日程", "1.0")
class CalendarPlugin(STAR):
    def __init__(self, context: Context):
        super().__init__(context)
        self.url = "https://asoul.love/calendar.ics"
        self.image_path = "data/asoul_schedule.png"
        self.font_path = os.path.join(os.path.dirname(__file__), "msyh.ttf")
        
        # 使用关键字参数确保兼容性
        try:
            self.context.register_task(
                cron="0 */12 * * *", 
                func=self.update_calendar_image, 
                desc="A-SOUL 日程更新"
            )
        except:
            # 如果还报错，尝试纯位置参数
            try:
                self.context.register_task("0 */12 * * *", self.update_calendar_image, "A-SOUL 日程更新")
            except:
                pass

    def parse_summary_v3(self, summary_text):
        """核心解析逻辑 V3：支持精准 Tag、名字、标题拆分"""
        types = ["突击", "2D", "日常", "节目"]
        found_tag = "日常" # 兜底类型
        found_name = "团播/夜谈"
        found_title = summary_text
        
        # 使用正则匹配 【】 和 名字部分
        # 例子: SUMMARY:【突击】贝拉突击: 【突击】一起看前瞻
        match = re.search(r"^【(.*?)】(.*?)[:：]\s*(.*)", summary_text)
        if match:
            raw_tag_part = match.group(1) # '突击'
            name_part = match.group(2) # '贝拉突击'
            found_title = match.group(3) # '【突击】一起看前瞻'
            
            # 1. 确定直播类型
            for t in types:
                if t in raw_tag_part:
                    found_tag = t
                    break
                    
            # 2. 优化名字显示 (处理类似 "贝拉突击" 里的 "突击" 赘余)
            found_name = name_part.replace("突击", "").replace("日常", "").strip()
            
            return found_tag, found_name, found_title
        else:
            # 如果不符合标准格式，使用模糊识别
            for t in types:
                if t in summary_text:
                    found_tag = t
                    break
            return found_tag, "团播/夜谈", summary_text

    def parse_ics_advanced(self, text):
        """使用 ics 库进行稳定解析"""
        all_events = []
        try:
            calendar = Calendar(text)
            for event in sorted(calendar.events):
                summary = event.name
                dtstart = event.begin
                location = event.location if hasattr(event, 'location') else ""
                
                if not dtstart or not summary: continue
                
                # 状态识别
                is_canceled = any(kw in summary for kw in ["取消", "延期", "更改"])
                
                # 深度解析 SUMMARY
                tag, name, title = self.parse_summary_v2(summary)
                
                # ICS 的 begin 是 UTC arrow 对象，直接转换
                bj_dt = dtstart.to('Asia/Shanghai').datetime.replace(tzinfo=None)
                all_events.append({
                    "time": bj_dt,
                    "tag": tag,
                    "name": name,
                    "title": title,
                    "url": location,
                    "canceled": is_canceled
                })
        except:
            return [] # 解析失败返回空
            
        return sorted(all_events, key=lambda x: x["time"])

    def get_color(self, url, title):
        group_color = "#5C6370" # 兜底团播色
        mapping = {"22637261": "#E799B0", "22625027": "#576690", "22632424": "#DB7D74", "30849777": "#C93773", "30858592": "#7252C0"}
        found = False
        for uid, color in mapping.items():
            if uid in url: return color
        kws = {"嘉然": "#E799B0", "贝拉": "#DB7D74", "乃琳": "#576690", "思诺": "#7252C0", "心宜": "#C93773"}
        for k, v in kws.items():
            if k in title: return v
        return group_color

    def draw_event_card(self, draw, base_img, x, y, ev, fonts):
        """精准手绘单个日程块，复刻例图排版"""
        COL_W = 280
        MARGIN_C = 45 # 卡片右侧留白
        
        # 1. 绘制左侧灰色指示线
        draw.line([x - 18, y, x - 18, y + 200], fill="#DCDFE6", width=2)
        
        # 2. 时间文字
        time_color = "#99A2AA" if ev["canceled"] else "#666666"
        draw.text((x, y), ev["time"].strftime('%H:%M'), fill=time_color, font=fonts['time'])
        
        y_card = y + 35
        # 计算卡片内的多行标题
        title_txt = ev["title"]
        # PIL 计算多行文本逻辑简易版
        lines = []
        if len(title_txt) > 13: # 根据像素估算
            lines.append(title_txt[:12])
            # 支持最多三行标题
            if len(title_txt) > 26:
                lines.append(title_txt[12:25])
                lines.append(title_txt[25:35] + ("..." if len(title_txt) > 35 else ""))
            else:
                lines.append(title_txt[12:])
        else:
            lines.append(title_txt)
            
        line_count = len(lines)
        
        # 根据标题行数动态计算卡片高度和总高度
        # 基准高度 105 (两行标题) -> 一行 90 -> 三行 125
        card_h = 105
        if line_count == 1: card_h = 90
        elif line_count == 3: card_h = 125
        
        card_rect = [x, y_card, x +
