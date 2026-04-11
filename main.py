import httpx
import json
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
        self.cache_path = "data/asoul_events_cache.json"
        self.font_path = os.path.join(os.path.dirname(__file__), "msyh.ttf")
        
        try:
            self.context.register_task("0 */12 * * *", self.update_calendar_image)
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

    def parse_ics_to_dict(self, text):
        events_dict = {}
        # 处理 ICS 特有的换行折叠（即行首为空格表示上一行的延续）
        text = re.sub(r'\r\n\s', '', text) 
        text = re.sub(r'\n\s', '', text)
        
        # 提取每个 VEVENT 块
        vevent_blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S)
        
        for block in vevent_blocks:
            # 在当前块内提取信息
            uid_match = re.search(r"UID:(.*?)(?:\r|\n)", block)
            summary_match = re.search(r"SUMMARY:(.*?)(?:\r|\n)", block)
            dtstart_match = re.search(r"DTSTART:(.*?)(?:\r|\n)", block)
            url_match = re.search(r"URL:(.*?)(?:\r|\n)", block)
            
            uid = uid_match.group(1).strip() if uid_match else None
            summary = summary_match.group(1).strip() if summary_match else ""
            dtstart = dtstart_match.group(1).strip() if dtstart_match else None
            url = url_match.group(1).strip() if url_match else ""
            
            if not uid or not dtstart:
                continue
            
            tag, name, title = self.parse_summary_v3(summary)
            try:
                # 处理时间格式 (可能是 20260411T070000Z 或 20260411T070000)
                t_str = dtstart.replace('Z', '')[:15]
                bj_dt = datetime.strptime(t_str, "%Y%m%dT%H%M%S") + timedelta(hours=8)
                
                events_dict[uid] = {
                    "uid": uid,
                    "time": bj_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "tag": tag,
                    "name": name,
                    "title": title,
                    "url": url # 这里保存了当前块内的 URL
                }
            except:
                continue
        return events_dict

    def load_cached_events(self):
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except: return {}
        return {}

    def save_events(self, events):
        os.makedirs("data", exist_ok=True)
        with open(self.cache_path, 'w', encoding='utf-8') as f:
            json.dump(events, f, ensure_ascii=False, indent=2)

    def get_color(self, url):
        # 颜色与直播间号严格匹配
        mapping = {
            "22637261": "#E799B0", # 嘉然
            "22625027": "#576690", # 乃琳
            "22632424": "#DB7D74", # 贝拉
            "30849777": "#C93773", # 心宜
            "30858592": "#7252C0"  # 思诺
        }
        for room_id, color in mapping.items():
            if room_id in url:
                return color
        return "#5C6370" # 团播/夜谈默认灰色

    def draw_card(self, draw, base_img, x, y, ev, fonts):
        COL_W = 240
        draw.line([x - 15, y + 10, x - 15, y + 180], fill="#DCDFE6", width=2)
        draw.text((x, y), datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S").strftime('%H:%M'), fill="#99A2AA", font=fonts['time'])
        
        title = ev["title"]
        if len(title) > 33: title = title[:32] + "..."
        lines = [title[i:i+9] for i in range(0, len(title), 9)][:3]
        
        card_h = 85 + (len(lines) - 1) * 25
        y_c = y + 35
        
        # 颜色匹配：通过当前日程对象保存的 URL 进行判定
        is_canceled = ev.get("canceled", False)
        m_clr = "#E0E0E0" if is_canceled else self.get_color(ev["url"])
        
        draw.rounded_rectangle([x, y_c, x + COL_W - 30, y_c + card_h], radius=15, fill=m_clr)
        
        # 绘制半透明 Tag 框
        tag_canvas = PILImage.new('RGBA', base_img.size, (255, 255, 255, 0))
        tag_draw = ImageDraw.Draw(tag_canvas)
        tag_draw.rounded_rectangle([x+10, y_c+15, x+65, y_c+43], radius=8, fill=(255, 255, 255, 60))
        base_img.paste(tag_canvas, (0, 0), tag_canvas)
        
        draw.text((x + 18, y_c + 18), ev["tag"], fill="#FFFFFF", font=fonts['tag'])
        draw.text((x + 75, y_c + 16), ev["name"], fill="#FFFFFF", font=fonts['name'])
        for i, line in enumerate(lines):
            draw.text((x + 10, y_c + 50 + i * 25), line, fill="#FFFFFF", font=fonts['title'])
            
        if is_canceled:
            line_y_mid = y_c + card_h // 2
            draw.line([x + 10, line_y_mid, x + COL_W - 40, line_y_mid], fill="#444444", width=3)
        return card_h + 55

    async def update_calendar_image(self):
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(self.url, timeout=10)
                new_events = self.parse_ics_to_dict(resp.text)
                all_events = self.load_cached_events()
                all_events.update(new_events) # 合并新旧日程
                self.save_events(all_events)
                
                render_list = list(all_events.values())
                render_list.sort(key=lambda x: x["time"])
                
                # 同天重复项标记（保留最晚的，划掉较早的）
                for i in range(len(render_list)):
                    render_list[i]["canceled"] = False
                    for j in range(i + 1, len(render_list)):
                        if render_list[i]["title"] == render_list[j]["title"] and \
                           render_list[i]["time"][:10] == render_list[j]["time"][:10]:
                            render_list[i]["canceled"] = True
            except: return False
            
            # 筛选本周数据进行绘制
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start_w = today - timedelta(days=today.weekday())
            w_data = {i: [] for i in range(7)}
            for e in render_list:
                ev_dt = datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S")
                diff = (ev_dt.date() - start_w.date()).days
                if 0 <= diff <= 6:
                    w_data[diff].append(e)

            CW, MT = 260, 180
            day_heights = []
            for i in range(7):
                h = 0
                for ev in w_data[i]:
                    lines_count = len([ev["title"][k:k+9] for k in range(0, len(ev["title"]), 9)][:3])
                    h += (85 + (lines_count - 1) * 25 + 55)
                day_heights.append(h)
            
            img_h = MT + (max(day_heights) if day_heights else 100) + 60
            img = PILImage.new('RGB', (CW * 7 + 80, int(img_h)), color="#F4F5F7")
            draw = ImageDraw.Draw(img)
            
            try:
                fonts = {
                    'header': ImageFont.truetype(self.font_path, 48),
                    'date': ImageFont.truetype(self.font_path, 22),
                    'time': ImageFont.truetype(self.font_path, 19),
                    'tag': ImageFont.truetype(self.font_path, 17),
                    'name': ImageFont.truetype(self.font_path, 20),
                    'title': ImageFont.truetype(self.font_path, 19)
                }
            except: return False

            draw.text((CW*3.5-80, 50), "本 周 日 程", fill="#222222", font=fonts['header'])
            w_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            for i in range(7):
                x = 55 + i * CW
                curr_d = start_w + timedelta(days=i)
                d_clr = "#00AEEC" if i == datetime.now().weekday() else "#666666"
                draw.text((x, 120), curr_d.strftime('%m/%d'), fill=d_clr, font=fonts['date'])
                draw.text((x + 85, 120), w_names[i], fill=d_clr, font=fonts['date'])
                y_o = MT
                for ev in w_data[i]:
                    y_o += self.draw_card(draw, img, x, y_o, ev, fonts)
            
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
        else: yield event.plain_result("更新失败，请检查网络或日志。")
