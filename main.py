import httpx
from PIL import Image as PILImage, ImageDraw, ImageFont
from datetime import datetime, timedelta
import os, re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *

@register("asoul_calendar", "awslYui", "A-SOUL 日程", "1.0")
class CalendarPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.url = "https://asoul.love/calendar.ics"
        self.image_path = "data/asoul_schedule.png"
        self.font_path = os.path.join(os.path.dirname(__file__), "msyh.ttf")
        
        # 兼容性注册
        try:
            self.context.register_task("0 */12 * * *", self.update_calendar_image, "A-SOUL 日程更新")
        except:
            pass

    def parse_summary_v3(self, text):
        types = ["突击", "2D", "日常", "节目"]
        found_tag, found_name, found_title = "日常", "团播/夜谈", text
        
        match = re.search(r"^【(.*?)】(.*?)[:：]\s*(.*)", text)
        if match:
            raw_tag, name_part, found_title = match.group(1), match.group(2), match.group(3)
            for t in types:
                if t in raw_tag: found_tag = t
            found_name = name_part.replace("突击", "").replace("日常", "").strip()
        return found_tag, found_name, found_title

    def parse_ics_advanced(self, text):
        events = []
        items = re.findall(r"BEGIN:VEVENT.*?END:VEVENT", text, re.S)
        for item in items:
            summary = re.search(r"SUMMARY:(.*)", item).group(1).strip() if re.search(r"SUMMARY:(.*)", item) else ""
            dtstart = re.search(r"DTSTART:(.*)", item).group(1).strip() if re.search(r"DTSTART:(.*)", item) else ""
            location = re.search(r"LOCATION:(.*)", item).group(1).strip() if re.search(r"LOCATION:(.*)", item) else ""
            if not dtstart: continue
            
            tag, name, title = self.parse_summary_v3(summary)
            is_canceled = any(kw in summary for kw in ["取消", "延期", "更改"])
            try:
                t_str = dtstart[:16].replace('Z','')
                bj_dt = datetime.strptime(t_str, "%Y%m%dT%H%M%S") + timedelta(hours=8)
                events.append({"time": bj_dt, "tag": tag, "name": name, "title": title, "url": location, "canceled": is_canceled})
            except: continue
        return sorted(events, key=lambda x: x["time"])

    def get_color(self, url, name):
        mapping = {"22637261": "#E799B0", "22625027": "#576690", "22632424": "#DB7D74", "30849777": "#C93773", "30858592": "#7252C0"}
        for uid, color in mapping.items():
            if uid in url: return color
        kws = {"嘉然": "#E799B0", "贝拉": "#DB7D74", "乃琳": "#576690", "思诺": "#7252C0", "心宜": "#C93773"}
        for k, v in kws.items():
            if k in name: return v
        return "#5C6370"

    def draw_card(self, draw, base_img, x, y, ev, fonts):
        COL_W = 280
        # 1. 左侧灰色指示线 (例图排版)
        draw.line([x - 18, y + 10, x - 18, y + 180], fill="#DCDFE6", width=2)
        # 2. 时间
        draw.text((x, y), ev["time"].strftime('%H:%M'), fill="#99A2AA", font=fonts['time'])
        
        # 3. 动态换行逻辑
        title = ev["title"]
        lines = [title[i:i+11] for i in range(0, len(title), 11)][:3] # 每行11字，最多3行
        card_h = 85 + (len(lines) - 1) * 25
        
        y_c = y + 35
        m_clr = "#E0E0E0" if ev["canceled"] else self.get_color(ev["url"], ev["name"])
        draw.rounded_rectangle([x, y_c, x + COL_W - 45, y_c + card_h], radius=15, fill=m_clr)
        
        # 4. 半透明 Tag (复刻例图2)
        tag_canvas = PILImage.new('RGBA', base_img.size, (255, 255, 255, 0))
        tag_draw = ImageDraw.Draw(tag_canvas)
        tag_draw.rounded_rectangle([x+15, y_c+15, x+75, y_c+43], radius=8, fill=(255, 255, 255, 60))
        base_img.paste(tag_canvas, (0, 0), tag_canvas)
        
        draw.text((x + 25, y_c + 18), ev["tag"], fill="#FFFFFF", font=fonts['tag'])
        draw.text((x + 90, y_c + 16), ev["name"], fill="#FFFFFF", font=fonts['name'])
        
        # 5. 绘制标题
        for i, line in enumerate(lines):
            draw.text((x + 15, y_c + 50 + i * 25), line, fill="#FFFFFF", font=fonts['title'])
            
        if ev["canceled"]:
            draw.line([x + 10, y_c + card_h//2, x + COL_W - 55, y_c + card_h//2], fill="#444444", width=3)
        return card_h + 60

    async def update_calendar_image(self):
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(self.url, timeout=10)
                all_ev = self.parse_ics_advanced(resp.text)
            except: return False
            
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start_w = today - timedelta(days=today.weekday())
            w_data = {i: [] for i in range(7)}
            for e in all_ev:
                diff = (e["time"].date() - start_w.date()).days
                if 0 <= diff <= 6: w_data[diff].append(e)

            # 画布
            CW, MT = 300, 180
            max_c = max([len(v) for v in w_data.values()] + [1])
            img = PILImage.new('RGB', (CW * 7 + 100, MT + max_c * 220 + 100), color="#F4F5F7")
            draw = ImageDraw.Draw(img)
            
            try:
                fonts = {
                    'header': ImageFont.truetype(self.font_path, 48), # 标题加粗加大
                    'date': ImageFont.truetype(self.font_path, 24),   # 日期加粗
                    'time': ImageFont.truetype(self.font_path, 20),
                    'tag': ImageFont.truetype(self.font_path, 18),
                    'name': ImageFont.truetype(self.font_path, 22),
                    'title': ImageFont.truetype(self.font_path, 20)
                }
            except: return False

            # 绘制大标题
            draw.text((CW*3.5-80, 50), "本 周 日 程", fill="#222222", font=fonts['header'])
            w_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            
            for i in range(7):
                x = 65 + i * CW
                curr_d = start_w + timedelta(days=i)
                d_clr = "#00AEEC" if i == datetime.now().weekday() else "#666666"
                # 绘制日期和周几
                draw.text((x, 120), curr_d.strftime('%Y/%m/%d'), fill=d_clr, font=fonts['date'])
                draw.text((x + 150, 120), w_names[i], fill=d_clr, font=fonts['date'])
                
                y_o = MT
                for ev in w_data[i]:
                    y_o += self.draw_card(draw, img, x, y_o, ev, fonts)
            
            os.makedirs("data", exist_ok=True)
            img.save(self.image_path)
            return True

    @filter.command("日程表")
    async def send_calendar(self, event: AstrMessageEvent):
        if os.path.exists(self.image_path): yield event.image_result(self.image_path)
        else: yield event.plain_result("请执行 /更新日程表")

    @filter.command("更新日程表")
    async def force_update(self, event: AstrMessageEvent):
        yield event.plain_result("正在更新日程表...")
        if await self.update_calendar_image(): yield event.image_result(self.image_path)
        else: yield event.plain_result("同步失败，请检查字体。")
