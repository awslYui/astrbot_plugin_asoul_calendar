import httpx
import json
from PIL import Image as PILImage, ImageDraw, ImageFont
from datetime import datetime, timedelta
import os, re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *

@register("asoul_calendar", "awslYui", "A-SOUL 日程", "1.1")
class CalendarPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.url = "https://asoul.love/calendar.ics"
        self.data_dir = "data/asoul_calendar"
        os.makedirs(self.data_dir, exist_ok=True)
        self.cache_path = os.path.join(self.data_dir, "asoul_events_cache.json")
        self.font_path = os.path.join(os.path.dirname(__file__), "msyh.ttf")
        
        try:
            # 每12小时自动更新一次本周和下周的图
            self.context.register_task("0 */12 * * *", lambda: self.update_calendar_image(0))
            self.context.register_task("5 */12 * * *", lambda: self.update_calendar_image(1))
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
        text = re.sub(r'\r?\n\s', '', text) 
        vevent_blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S)
        for block in vevent_blocks:
            uid = re.search(r"^UID:(.*?)$", block, re.M)
            summary = re.search(r"^SUMMARY:(.*?)$", block, re.M)
            dtstart = re.search(r"^DTSTART:(.*?)$", block, re.M)
            url_field = re.search(r"^URL:(.*?)$", block, re.M)
            u_id = uid.group(1).strip() if uid else None
            sum_text = summary.group(1).strip() if summary else ""
            t_start = dtstart.group(1).strip() if dtstart else None
            actual_url = url_field.group(1).strip() if url_field else ""
            if not u_id or not t_start: continue
            tag, name, title = self.parse_summary_v3(sum_text)
            try:
                t_str = t_start.replace('Z', '')[:15]
                bj_dt = datetime.strptime(t_str, "%Y%m%dT%H%M%S") + timedelta(hours=8)
                events_dict[u_id] = {
                    "uid": u_id, "time": bj_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "tag": tag, "name": name, "title": title, "url": actual_url
                }
            except: continue
        return events_dict

    def get_color(self, url):
        mapping = {
            "22637261": "#E799B0", "22625027": "#576690", "22632424": "#DB7D74",
            "30849777": "#C93773", "30858592": "#7252C0"
        }
        for room_id, color in mapping.items():
            if room_id in url: return color
        return "#5C6370"

    def draw_card(self, draw, base_img, x, y, ev, fonts):
        COL_W = 240
        draw.line([x - 15, y + 10, x - 15, y + 180], fill="#DCDFE6", width=2)
        draw.text((x, y), datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S").strftime('%H:%M'), fill="#99A2AA", font=fonts['time'])
        title = ev["title"]
        if len(title) > 33: title = title[:32] + "..."
        lines = [title[i:i+9] for i in range(0, len(title), 9)][:3]
        card_h = 85 + (len(lines) - 1) * 25
        y_c = y + 35
        is_canceled = ev.get("canceled", False)
        m_clr = "#E0E0E0" if is_canceled else self.get_color(ev["url"])
        draw.rounded_rectangle([x, y_c, x + COL_W - 30, y_c + card_h], radius=15, fill=m_clr)
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

    async def update_calendar_image(self, week_offset=0):
        """
        week_offset: 0 代表本周, 1 代表下周
        """
        suffix = "this_week" if week_offset == 0 else "next_week"
        save_path = os.path.join(self.data_dir, f"schedule_{suffix}.png")
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(self.url, timeout=10)
                new_evs = self.parse_ics_to_dict(resp.text)
                all_evs = self.load_cached_events()
                all_evs.update(new_evs)
                self.save_events(all_evs)
                
                render_list = list(all_evs.values())
                render_list.sort(key=lambda x: x["time"])
                # ... (保留原有的 canceled 判定逻辑)
            except Exception as e:
                print(f"Update error: {e}")
                return None
            
            # --- 核心修改：计算起始日期 ---
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            # 计算当前周一，然后加上 offset * 7 天
            start_of_week = (today - timedelta(days=today.weekday())) + timedelta(weeks=week_offset)
            
            w_data = {i: [] for i in range(7)}
            for e in render_list:
                ev_dt = datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S")
                diff = (ev_dt.date() - start_of_week.date()).days
                if 0 <= diff <= 6: 
                    w_data[diff].append(e)

            CW, MT = 260, 180
            day_heights = [sum([(85 + (len([ev["title"][k:k+9] for k in range(0, len(ev["title"]), 9)][:3]) - 1) * 25 + 55) for ev in w_data[i]]) for i in range(7)]
            img_h = MT + (max(day_heights) if day_heights else 100) + 60
            img = PILImage.new('RGB', (CW * 7 + 80, int(img_h)), color="#F4F5F7")
            draw = ImageDraw.Draw(img)
            
            try:
                fonts = {
                    'header': ImageFont.truetype(self.font_path, 52),
                    'update': ImageFont.truetype(self.font_path, 18),
                    'date': ImageFont.truetype(self.font_path, 22),
                    'time': ImageFont.truetype(self.font_path, 19),
                    'tag': ImageFont.truetype(self.font_path, 17),
                    'name': ImageFont.truetype(self.font_path, 20),
                    'title': ImageFont.truetype(self.font_path, 19)
                }
            except: return False

            # --- 绘制超粗大标题 ---
            header_text = "本 周 日 程" if week_offset == 0 else "下 周 日 程"
            hx, hy = CW*3.5-120, 50
            # 模拟加粗：在四周偏移1像素绘制多次
            for off_x in range(-1, 2):
                for off_y in range(-1, 2):
                    draw.text((hx + off_x, hy + off_y), header_text, fill="#222222", font=fonts['header'])
            
            # --- 绘制更新时间 ---
            update_str = f"更新于: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            draw.text((hx + 280, hy + 32), update_str, fill="#99A2AA", font=fonts['update'])

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

            img.save(save_path)
            return save_path

    @filter.command("日程表")
    async def send_calendar(self, event: AstrMessageEvent, week_type: str = "本周"):
        '''获取日程表。可选参数：本周、下周'''
        offset = 1 if week_type == "下周" else 0
        suffix = "this_week" if offset == 0 else "next_week"
        target_path = os.path.join(self.data_dir, f"schedule_{suffix}.png")

        if os.path.exists(target_path):
            yield event.image_result(target_path)
        else:
            yield event.plain_result(f"本地暂无{week_type}日程缓存，正在为您生成...")
            path = await self.update_calendar_image(offset)
            if path:
                yield event.image_result(path)
            else:
                yield event.plain_result("生成失败，请检查网络或稍后再试。")

    @filter.command("更新日程表")
    async def force_update(self, event: AstrMessageEvent):
        yield event.plain_result("正在更新本周及下周日程表...")
        p1 = await self.update_calendar_image(0)
        p2 = await self.update_calendar_image(1)
        if p1 and p2:
            yield event.plain_result("更新成功！")
            yield event.image_result(p1)
        else:
            yield event.plain_result("更新部分失败。")
