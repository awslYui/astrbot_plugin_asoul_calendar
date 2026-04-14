import httpx
import json
from PIL import Image as PILImage, ImageDraw, ImageFont
from datetime import datetime, timedelta
import os, re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *

@register("asoul_calendar", "awslYui", "A-SOUL 日程", "1.2")
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
        # 预处理：合并折行
        text = re.sub(r'\r?\n\s', '', text) 
        vevent_blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S)
        for block in vevent_blocks:
            # 使用 findall 并取最后一个，防止脏数据嵌套
            uids = re.findall(r"^UID:(.*?)$", block, re.M)
            summaries = re.findall(r"^SUMMARY:(.*?)$", block, re.M)
            dtstarts = re.findall(r"^DTSTART:(.*?)$", block, re.M)
            urls = re.findall(r"^URL:(.*?)$", block, re.M)
            dtstamps = re.findall(r"^DTSTAMP:(.*?)$", block, re.M)
            
            u_id = uids[-1].strip() if uids else None
            sum_text = summaries[-1].strip() if summaries else ""
            t_start = dtstarts[-1].strip() if dtstarts else None
            actual_url = urls[-1].strip() if urls else ""
            t_stamp = dtstamps[-1].strip() if dtstamps else "19700101T000000Z"
            
            if not u_id or not t_start: continue
            tag, name, title = self.parse_summary_v3(sum_text)
            try:
                t_str = t_start.replace('Z', '')[:15]
                bj_dt = datetime.strptime(t_str, "%Y%m%dT%H%M%S") + timedelta(hours=8)
                events_dict[u_id] = {
                    "uid": u_id, 
                    "time": bj_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "dtstamp": t_stamp,
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
        
        # 判定是否变灰
        is_canceled = ev.get("canceled", False)
        m_clr = "#E0E0E0" if is_canceled else self.get_color(ev["url"])
        
        draw.rounded_rectangle([x, y_c, x + COL_W - 30, y_c + card_h], radius=15, fill=m_clr)
        
        # 绘制半透明标签背景
        tag_canvas = PILImage.new('RGBA', base_img.size, (255, 255, 255, 0))
        tag_draw = ImageDraw.Draw(tag_canvas)
        tag_draw.rounded_rectangle([x+10, y_c+15, x+65, y_c+43], radius=8, fill=(255, 255, 255, 60))
        base_img.paste(tag_canvas, (0, 0), tag_canvas)
        
        draw.text((x + 18, y_c + 18), ev["tag"], fill="#FFFFFF", font=fonts['tag'])
        draw.text((x + 75, y_c + 16), ev["name"], fill="#FFFFFF", font=fonts['name'])
        for i, line in enumerate(lines):
            draw.text((x + 10, y_c + 50 + i * 25), line, fill="#FFFFFF", font=fonts['title'])
            
        # 绘制删除线
        if is_canceled:
            line_y_mid = y_c + card_h // 2
            draw.line([x + 10, line_y_mid, x + COL_W - 40, line_y_mid], fill="#444444", width=3)
            
        return card_h + 55

    async def update_calendar_image(self, week_offset=0):
        suffix = "this" if week_offset == 0 else "next"
        image_path = os.path.join(self.data_dir, f"schedule_{suffix}.png")
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(self.url, timeout=15)
                new_evs = self.parse_ics_to_dict(resp.text)
                all_evs = self.load_cached_events()
                
                fresh_uids = set(new_evs.keys())
                
                # 1. 更新/新增抓取到的事件
                for uid, ev in new_evs.items():
                    ev["canceled"] = False 
                    all_evs[uid] = ev
                    
                # 2. 如果缓存中存在但新 ICS 中消失了，标记为已取消
                for uid, ev in all_evs.items():
                    if uid not in fresh_uids:
                        ev["canceled"] = True
                
                # 3. 排序并处理“同文件内冲突”（DTSTAMP 对比）
                render_list = list(all_evs.values())
                render_list.sort(key=lambda x: x["time"])
                
                for i in range(len(render_list)):
                    if render_list[i].get("canceled"): continue
                    for j in range(i + 1, len(render_list)):
                        if render_list[j].get("canceled"): continue
                        
                        # 同名且同天
                        if render_list[i]["title"] == render_list[j]["title"] and \
                           render_list[i]["time"][:10] == render_list[j]["time"][:10]:
                            
                            stamp_i = render_list[i].get("dtstamp", "0")
                            stamp_j = render_list[j].get("dtstamp", "0")
                            
                            # 谁的时间戳小（早），谁就被划掉
                            if stamp_i < stamp_j:
                                render_list[i]["canceled"] = True
                            else:
                                render_list[j]["canceled"] = True

                # 4. 缓存清理：只保留最近30天，防止 json 膨胀
                now_time = datetime.now()
                keys_to_del = [uid for uid, ev in all_evs.items() 
                               if (now_time - datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S")).days > 30]
                for k in keys_to_del: del all_evs[k]
                
                self.save_events(all_evs)
                
            except Exception as e:
                print(f"[asoul_calendar] 更新出错: {e}")
                return None
            
            # --- 绘图逻辑开始 ---
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start_w = (today - timedelta(days=today.weekday())) + timedelta(weeks=week_offset)
            
            w_data = {i: [] for i in range(7)}
            for e in render_list:
                ev_dt = datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S")
                diff = (ev_dt.date() - start_w.date()).days
                if 0 <= diff <= 6: w_data[diff].append(e)

            CW, MT = 260, 180
            # 动态计算高度
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
                if not os.path.exists(self.font_path):
                    f = ImageFont.load_default()
                    fonts = {k: f for k in ['header', 'update', 'date', 'time', 'tag', 'name', 'title']}
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

            header_text = "本 周 日 程" if week_offset == 0 else "下 周 日 程"
            hx, hy = CW*3.5-120, 50
            draw.text((hx, hy), header_text, fill="#222222", font=fonts['header'])
            
            update_str = f"更新于: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            draw.text((hx + 280, hy + 32), update_str, fill="#99A2AA", font=fonts['update'])

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
        '''获取日程表。用法：/日程表 [本周/下周]'''
        offset = 1 if week_type == "下周" else 0
        suffix = "this" if offset == 0 else "next"
        target_path = os.path.join(self.data_dir, f"schedule_{suffix}.png")

        if os.path.exists(target_path):
            yield event.image_result(target_path)
        else:
            yield event.plain_result(f"正在为您生成{week_type}日程表...")
            path = await self.update_calendar_image(offset)
            if path: yield event.image_result(path)
            else: yield event.plain_result("生成失败。")

    @filter.command("更新日程表")
    async def force_update(self, event: AstrMessageEvent):
        yield event.plain_result("正在更新日程表...")
        success_this = await self.update_calendar_image(0)
        if success_this:
            yield event.plain_result("更新成功！")
            yield event.image_result(success_this)
        else:
            yield event.plain_result("更新失败。")
