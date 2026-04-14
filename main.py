import httpx
import json
from PIL import Image as PILImage, ImageDraw, ImageFont
from datetime import datetime, timedelta
import os, re, traceback, logging
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *

# 设置插件专用日志
logger = logging.getLogger("astrbot_asoul_calendar")

@register("asoul_calendar", "awslYui", "A-SOUL 日程", "1.5")
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
            logger.error(f"自动任务注册失败: {e}")

    def get_core_title(self, title):
        """提取核心标题内容"""
        core = re.sub(r"【.*?】", "", title)
        core = re.sub(r"(\d+年\d+月\d+日.*场)", "", core)
        return core.strip()

    def parse_summary_v3(self, text):
        types = ["突击", "2D", "日常", "节目", "线下", "3D"]
        found_tag, found_name, found_title = "日常", "团播", text
        match = re.search(r"^【(.*?)】(.*?)([:：]\s*(.*))?$", text)
        if match:
            raw_tag, name_part, _, title_part = match.groups()
            for t in types:
                if t in raw_tag: found_tag = t
            found_name = name_part.replace("突击", "").replace("直播", "").strip()
            found_title = title_part.strip() if title_part else name_part
        return found_tag, found_name, found_title

    def parse_ics_to_dict(self, text):
        events_dict = {}
        text = re.sub(r'\r?\n\s', '', text) 
        vevent_blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S)
        for block in vevent_blocks:
            try:
                uid_m = re.search(r"^UID:(.*?)$", block, re.M)
                sum_m = re.search(r"^SUMMARY:(.*?)$", block, re.M)
                start_m = re.search(r"^DTSTART:(.*?)$", block, re.M)
                stamp_m = re.search(r"^DTSTAMP:(.*?)$", block, re.M)
                url_m = re.search(r"^URL:(.*?)$", block, re.M)

                if not uid_m or not start_m: continue
                
                u_id = uid_m.group(1).strip()
                sum_text = sum_m.group(1).strip() if sum_m else ""
                t_start = start_m.group(1).strip()
                t_stamp = stamp_m.group(1).strip() if stamp_m else "0"
                actual_url = url_m.group(1).strip() if url_m else ""

                tag, name, title = self.parse_summary_v3(sum_text)
                t_str = t_start.replace('Z', '')[:15]
                bj_dt = datetime.strptime(t_str, "%Y%m%dT%H%M%S") + timedelta(hours=8)
                events_dict[u_id] = {
                    "uid": u_id, "stamp": t_stamp,
                    "time": bj_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "tag": tag, "name": name, "title": title, "url": actual_url
                }
            except Exception as e:
                logger.warning(f"解析单个 VEVENT 出错: {e}")
                continue
        return events_dict

    def get_color(self, url):
        mapping = {"22637261": "#E799B0", "22625027": "#576690", "22632424": "#DB7D74", "30849777": "#C93773", "30858592": "#7252C0"}
        for room_id, color in mapping.items():
            if room_id in url: return color
        return "#5C6370"

    async def update_calendar_image(self, week_offset=0):
        suffix = "this" if week_offset == 0 else "next"
        image_path = os.path.join(self.data_dir, f"schedule_{suffix}.png")
        
        logger.info(f"开始更新日程图: {suffix}")
        async with httpx.AsyncClient() as client:
            try:
                # 1. 网络请求阶段
                resp = await client.get(self.url, timeout=15)
                if resp.status_code != 200:
                    logger.error(f"ICS文件下载失败，HTTP状态码: {resp.status_code}")
                    return None
                
                # 2. 解析与冲突处理阶段
                new_evs = self.parse_ics_to_dict(resp.text)
                if not new_evs:
                    logger.error("解析 ICS 结果为空，请检查数据格式或正则匹配")
                    return None
                
                all_evs = self.load_cached_events()
                all_evs.update(new_evs)
                self.save_events(all_evs)
                
                render_list = list(all_evs.values())
                for ev in render_list: ev["canceled"] = False
                render_list.sort(key=lambda x: x["stamp"])

                # 冲突逻辑日志
                content_map = {}
                for idx, ev in enumerate(render_list):
                    core = self.get_core_title(ev["title"])
                    content_key = f"{ev['name']}_{core}"
                    if content_key in content_map:
                        logger.debug(f"检测到内容冲突，划掉旧直播: {render_list[content_map[content_key]]['title']}")
                        render_list[content_map[content_key]]["canceled"] = True
                    content_map[content_key] = idx

                slot_map = {}
                for idx, ev in enumerate(render_list):
                    if ev["canceled"]: continue
                    slot_key = f"{ev['name']}_{ev['time']}"
                    if slot_key in slot_map:
                        logger.debug(f"检测到档期覆盖，划掉旧安排: {render_list[slot_map[slot_key]]['title']}")
                        render_list[slot_map[slot_key]]["canceled"] = True
                    slot_map[slot_key] = idx

                # 3. 绘图准备阶段
                today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                start_w = (today - timedelta(days=today.weekday())) + timedelta(weeks=week_offset)
                
                w_data = {i: [] for i in range(7)}
                for e in render_list:
                    ev_dt = datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S")
                    diff = (ev_dt.date() - start_w.date()).days
                    if 0 <= diff <= 6: w_data[diff].append(e)

                for i in range(7): w_data[i].sort(key=lambda x: x["time"])

                CW, MT = 260, 180
                day_heights = [sum([(85 + (len([ev["title"][k:k+9] for k in range(0, len(ev["title"]), 9)][:3]) - 1) * 25 + 55) for ev in w_data[i]]) for i in range(7)]
                img_h = max(MT + max(day_heights if day_heights else [100]) + 80, 600)
                
                img = PILImage.new('RGB', (CW * 7 + 80, int(img_h)), color="#F4F5F7")
                draw = ImageDraw.Draw(img)
                
                # 4. 字体与渲染阶段
                if not os.path.exists(self.font_path):
                    logger.warning(f"未找到字体文件 {self.font_path}，将使用系统默认字体")
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

                # 绘制页眉和卡片 (代码略，同前版本...)
                # ... [此处保持之前 draw_card 调用和循环绘制逻辑] ...
                header_text = "本 周 日 程" if week_offset == 0 else "下 周 日 程"
                draw.text((CW*3.5-120, 50), header_text, fill="#222222", font=fonts['header'])
                
                for i in range(7):
                    x, curr_d = 55 + i * CW, start_w + timedelta(days=i)
                    d_clr = "#00AEEC" if curr_d.date() == datetime.now().date() else "#666666"
                    draw.text((x, 120), curr_d.strftime('%m/%d'), fill=d_clr, font=fonts['date'])
                    draw.text((x + 85, 120), ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][i], fill=d_clr, font=fonts['date'])
                    y_o = MT
                    for ev in w_data[i]:
                        y_o += self.draw_card(draw, img, x, y_o, ev, fonts)
                
                img.save(image_path)
                logger.info(f"日程图更新成功: {image_path}")
                return image_path

            except Exception as e:
                logger.error(f"更新日程图过程发生未捕获异常:\n{traceback.format_exc()}")
                return None

    def draw_card(self, draw, base_img, x, y, ev, fonts):
        # 内部逻辑不变，已包含在完整代码中
        COL_W = 240
        is_canceled = ev.get("canceled", False)
        m_clr = "#DCDFE6" if is_canceled else self.get_color(ev["url"])
        txt_main = "#909399" if is_canceled else "#FFFFFF"
        draw.line([x - 15, y + 10, x - 15, y + 180], fill="#DCDFE6", width=2)
        draw.text((x, y), datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S").strftime('%H:%M'), fill="#99A2AA", font=fonts['time'])
        title = ev["title"]
        lines = [title[i:i+9] for i in range(0, len(title), 9)][:3]
        card_h = 85 + (len(lines) - 1) * 25
        y_c = y + 35
        draw.rounded_rectangle([x, y_c, x + COL_W - 30, y_c + card_h], radius=15, fill=m_clr)
        tag_canvas = PILImage.new('RGBA', base_img.size, (255, 255, 255, 0))
        tag_draw = ImageDraw.Draw(tag_canvas)
        tag_draw.rounded_rectangle([x+10, y_c+15, x+65, y_c+43], radius=8, fill=(255, 255, 255, 60))
        base_img.paste(tag_canvas, (0, 0), tag_canvas)
        draw.text((x + 18, y_c + 18), ev["tag"], fill=txt_main, font=fonts['tag'])
        draw.text((x + 75, y_c + 16), ev["name"], fill=txt_main, font=fonts['name'])
        for i, line in enumerate(lines): draw.text((x + 10, y_c + 50 + i * 25), line, fill=txt_main, font=fonts['title'])
        if is_canceled: draw.line([x + 10, y_c + card_h // 2 + 5, x + COL_W - 40, y_c + card_h // 2 + 5], fill="#717375", width=3)
        return card_h + 55

    def load_cached_events(self):
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, 'r', encoding='utf-8') as f: return json.load(f)
            except Exception as e:
                logger.error(f"加载缓存失败: {e}")
                return {}
        return {}

    def save_events(self, events):
        try:
            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump(events, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存缓存失败: {e}")

    @filter.command("日程表")
    async def send_calendar(self, event: AstrMessageEvent, week_type: str = "本周"):
        offset = 1 if week_type == "下周" else 0
        path = await self.update_calendar_image(offset)
        if path: yield event.image_result(path)
        else: yield event.plain_result("日程表更新失败，请查看日志。")

    @filter.command("更新日程表")
    async def force_update(self, event: AstrMessageEvent):
        yield event.plain_result("正在更新日程表...")
        s1 = await self.update_calendar_image(0)
        s2 = await self.update_calendar_image(1)
        if s1 and s2: yield event.plain_result("更新成功！")
        else: yield event.plain_result("更新失败，请查看日志。")
