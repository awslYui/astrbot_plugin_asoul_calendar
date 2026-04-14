import httpx
import json
from PIL import Image as PILImage, ImageDraw, ImageFont
from datetime import datetime, timedelta
import os, re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *

@register("asoul_calendar", "awslYui", "A-SOUL 日程", "1.3")
class CalendarPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.url = "https://asoul.love/calendar.ics"
        self.data_dir = "data/asoul_calendar"
        os.makedirs(self.data_dir, exist_ok=True)
        self.cache_path = os.path.join(self.data_dir, "asoul_events_cache.json")
        self.font_path = os.path.join(os.path.dirname(__file__), "msyh.ttf")
        
        async def update_this_week(): 
            await self.update_calendar_image(0)
        async def update_next_week(): 
            await self.update_calendar_image(1)
        
        try:
            self.context.register_task("0 */12 * * *", update_this_week)
            self.context.register_task("5 */12 * * *", update_next_week)
        except Exception as e:
            print(f"[asoul_calendar] 自动任务注册失败: {e}")

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
        # 预处理：修复换行导致的字段中断
        text = re.sub(r'\r?\n\s', '', text) 
        vevent_blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S)
        for block in vevent_blocks:
            uid_m = re.search(r"^UID:(.*?)$", block, re.M)
            sum_m = re.search(r"^SUMMARY:(.*?)$", block, re.M)
            start_m = re.search(r"^DTSTART:(.*?)$", block, re.M)
            stamp_m = re.search(r"^DTSTAMP:(.*?)$", block, re.M)
            url_m = re.search(r"^URL:(.*?)$", block, re.M)
            desc_m = re.search(r"^DESCRIPTION:(.*?)$", block, re.M)

            if not uid_m or not start_m: continue
            
            u_id = uid_m.group(1).strip()
            sum_text = sum_m.group(1).strip() if sum_m else ""
            t_start = start_m.group(1).strip()
            t_stamp = stamp_m.group(1).strip() if stamp_m else "0"
            actual_url = url_m.group(1).strip() if url_m else ""
            desc_text = desc_m.group(1).strip() if desc_m else ""

            # 核心：提取动态 ID (Opus ID) 用于识别调整
            # 兼容 SUMMARY 和 DESCRIPTION 两个位置
            opus_id = ""
            opus_match = re.search(r"opus/(\d+)", desc_text + sum_text)
            if opus_match:
                opus_id = opus_match.group(1)
            
            tag, name, title = self.parse_summary_v3(sum_text)
            try:
                t_str = t_start.replace('Z', '')[:15]
                bj_dt = datetime.strptime(t_str, "%Y%m%dT%H%M%S") + timedelta(hours=8)
                events_dict[u_id] = {
                    "uid": u_id, 
                    "stamp": t_stamp,
                    "time": bj_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "tag": tag, "name": name, "title": title, "url": actual_url,
                    "opus_id": opus_id
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
        is_canceled = ev.get("canceled", False)
        
        # 视觉效果：已失效则使用灰色调
        m_clr = "#DCDFE6" if is_canceled else self.get_color(ev["url"])
        txt_main = "#909399" if is_canceled else "#FFFFFF"
        
        draw.line([x - 15, y + 10, x - 15, y + 180], fill="#DCDFE6", width=2)
        draw.text((x, y), datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S").strftime('%H:%M'), fill="#99A2AA", font=fonts['time'])
        
        title = ev["title"]
        if len(title) > 33: title = title[:32] + "..."
        lines = [title[i:i+9] for i in range(0, len(title), 9)][:3]
        card_h = 85 + (len(lines) - 1) * 25
        y_c = y + 35
        
        # 绘制卡片主体
        draw.rounded_rectangle([x, y_c, x + COL_W - 30, y_c + card_h], radius=15, fill=m_clr)
        
        # 绘制 Tag 半透明背景
        tag_canvas = PILImage.new('RGBA', base_img.size, (255, 255, 255, 0))
        tag_draw = ImageDraw.Draw(tag_canvas)
        tag_draw.rounded_rectangle([x+10, y_c+15, x+65, y_c+43], radius=8, fill=(255, 255, 255, 60))
        base_img.paste(tag_canvas, (0, 0), tag_canvas)
        
        draw.text((x + 18, y_c + 18), ev["tag"], fill=txt_main, font=fonts['tag'])
        draw.text((x + 75, y_c + 16), ev["name"], fill=txt_main, font=fonts['name'])
        for i, line in enumerate(lines):
            draw.text((x + 10, y_c + 50 + i * 25), line, fill=txt_main, font=fonts['title'])
            
        # 如果是被取消或调整过的，画中划线
        if is_canceled:
            line_y = y_c + card_h // 2 + 5
            draw.line([x + 10, line_y, x + COL_W - 40, line_y], fill="#717375", width=3)
            
        return card_h + 55

    async def update_calendar_image(self, week_offset=0):
        suffix = "this" if week_offset == 0 else "next"
        image_path = os.path.join(self.data_dir, f"schedule_{suffix}.png")
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(self.url, timeout=15)
                new_evs = self.parse_ics_to_dict(resp.text)
                all_evs = self.load_cached_events()
                all_evs.update(new_evs)
                self.save_events(all_evs)
                
                # --- 核心：多重冲突识别逻辑 ---
                render_list = list(all_evs.values())
                for ev in render_list: ev["canceled"] = False

                # 1. 识别 Opus ID (动态ID) 冲突
                # 如果多个 VEVENT 共享同一个 Opus ID，保留 DTSTAMP 最晚的
                opus_map = {}
                for idx, ev in enumerate(render_list):
                    oid = ev.get("opus_id")
                    if oid:
                        if oid not in opus_map:
                            opus_map[oid] = idx
                        else:
                            old_idx = opus_map[oid]
                            if render_list[idx]["stamp"] >= render_list[old_idx]["stamp"]:
                                render_list[old_idx]["canceled"] = True
                                opus_map[oid] = idx
                            else:
                                render_list[idx]["canceled"] = True

            except Exception as e:
                print(f"[asoul_calendar] 更新出错: {e}")
                return None
            
            # --- 绘图初始化 ---
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start_w = (today - timedelta(days=today.weekday())) + timedelta(weeks=week_offset)
            
            w_data = {i: [] for i in range(7)}
            for e in render_list:
                ev_dt = datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S")
                diff = (ev_dt.date() - start_w.date()).days
                if 0 <= diff <= 6: w_data[diff].append(e)

            # 按时间排序每一天内的日程
            for i in range(7):
                w_data[i].sort(key=lambda x: x["time"])

            CW, MT = 260, 180
            # 估算高度
            day_heights = []
            for i in range(7):
                h = sum([ (85 + (len([ev["title"][k:k+9] for k in range(0, len(ev["title"]), 9)][:3]) - 1) * 25 + 55) for ev in w_data[i]])
                day_heights.append(h)
            
            img_h = MT + (max(day_heights) if day_heights else 100) + 80
            img = PILImage.new('RGB', (CW * 7 + 80, int(img_h)), color="#F4F5F7")
            draw = ImageDraw.Draw(img)
            
            try:
                if not os.path.exists(self.font_path):
                    font_load = ImageFont.load_default()
                    fonts = {k: font_load for k in ['header', 'update', 'date', 'time', 'tag', 'name', 'title']}
                else:
                    fonts = {
                        'header': ImageFont.truetype(self.font_path, 52),
                        'update': ImageFont.truetype(self.font_path, 18),
                        'date': ImageFont.truetype(self.font_path, 22),
                        'time': ImageFont.truetype(self.font_path, 19),
                        'tag': ImageFont.truetype(self.font_path, 17),
                        'name': ImageFont.truetype(self.font_path, 20),
                        'title': ImageFont.truetype(self.font_path, 19)
                    }
            except: return None

            # 绘制头部
            header_text = "本 周 日 程" if week_offset == 0 else "下 周 日 程"
            draw.text((CW*3.5-120, 50), header_text, fill="#222222", font=fonts['header'])
            update_str = f"更新于: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            draw.text((CW*3.5+150, 82), update_str, fill="#99A2AA", font=fonts['update'])

            w_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            for i in range(7):
                x = 55 + i * CW
                curr_d = start_w + timedelta(days=i)
                d_clr = "#00AEEC" if curr_d.date() == datetime.now().date() else "#666666"
                draw.text((x, 120), curr_d.strftime('%m/%d'), fill=d_clr, font=fonts['date'])
                draw.text((x + 85, 120), w_names[i], fill=d_clr, font=fonts['date'])
                
                y_o = MT
                for ev in w_data[i]:
                    y_o += self.draw_card(draw, img, x, y_o, ev, fonts)
            
            img.save(image_path)
            return image_path

    def load_cached_events(self):
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, 'r', encoding='utf-8') as f: return json.load(f)
            except: return {}
        return {}

    def save_events(self, events):
        with open(self.cache_path, 'w', encoding='utf-8') as f:
            json.dump(events, f, ensure_ascii=False, indent=2)

    @filter.command("日程表")
    async def send_calendar(self, event: AstrMessageEvent, week_type: str = "本周"):
        offset = 1 if week_type == "下周" else 0
        suffix = "this" if offset == 0 else "next"
        target_path = os.path.join(self.data_dir, f"schedule_{suffix}.png")
        
        # 总是触发一次更新以保证准确性，如果网络慢可按需改为直接读取
        path = await self.update_calendar_image(offset)
        if path: yield event.image_result(path)
        else: yield event.plain_result("日程表生成失败，请检查网络或日志。")

    @filter.command("更新日程表")
    async def force_update(self, event: AstrMessageEvent):
        yield event.plain_result("正在更新日程表...")
        s1 = await self.update_calendar_image(0)
        s2 = await self.update_calendar_image(1)
        if s1: yield event.plain_result("更新成功！")
        else: yield event.plain_result("更新失败。")
