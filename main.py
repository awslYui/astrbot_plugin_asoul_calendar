import httpx
import json
from PIL import Image as PILImage, ImageDraw, ImageFont
from datetime import datetime, timedelta
import os, re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *

@register("asoul_calendar", "awslYui", "嘉然日程表", "1.1")
class CalendarPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.url = "https://asoul.love/calendar.ics"
        self.data_dir = "data/asoul_calendar"
        os.makedirs(self.data_dir, exist_ok=True)
        self.cache_path = os.path.join(self.data_dir, "diana_combined_cache.json")
        self.font_path = os.path.join(os.path.dirname(__file__), "msyh.ttf")
        
        async def update_all():
            await self.update_calendar_image(0)
            await self.update_calendar_image(1)
            await self.update_today_image()
            
        try:
            self.context.register_task("0 */6 * * *", update_all)
        except Exception as e:
            print(f"[asoul_calendar] 自动任务注册失败: {e}")

    def parse_summary_v3(self, text):
        types = ["突击", "2D", "日常", "节目"]
        found_tag, found_name, found_title = "日常", "嘉然", text
        match = re.search(r"^【(.*?)】(.*?)[:：]\s*(.*)", text)
        if match:
            raw_tag, name_part, found_title = match.group(1), match.group(2), match.group(3)
            for t in types:
                if t in raw_tag: found_tag = t
            found_name = name_part.replace("突击", "").replace("日常", "").strip()
        return found_tag, found_name, found_title

    def get_color(self, url):
        mapping = {
            "22637261": "#E799B0", # 嘉然
            "22625027": "#576690", # 乃琳
            "22632424": "#DB7D74", # 贝拉
            "30849777": "#C93773", # 官号/心宜
            "30858592": "#7252C0"  # 思诺
        }
        for room_id, color in mapping.items():
            if room_id in url: return color
        return "#E799B0" 

    def parse_ics_to_dict(self, text):
        events_dict = {}
        text = re.sub(r'\r?\n\s', '', text) 
        vevent_blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S)
        for block in vevent_blocks:
            summary = re.search(r"^SUMMARY:(.*?)$", block, re.M)
            sum_text = summary.group(1).strip() if summary else ""
            
            tag, name, title = self.parse_summary_v3(sum_text)
            
            is_diana = "嘉然" in sum_text
            is_group = "A-SOUL" in sum_text and tag == "节目"
            
            if not (is_diana or is_group):
                continue
            
            uid = re.search(r"^UID:(.*?)$", block, re.M)
            dtstart = re.search(r"^DTSTART:(.*?)$", block, re.M)
            url_field = re.search(r"^URL:(.*?)$", block, re.M)
            u_id = uid.group(1).strip() if uid else None
            t_start = dtstart.group(1).strip() if dtstart else None
            actual_url = url_field.group(1).strip() if url_field else ""
            
            if not u_id or not t_start: continue
            try:
                t_str = t_start.replace('Z', '')[:15]
                bj_dt = datetime.strptime(t_str, "%Y%m%dT%H%M%S") + timedelta(hours=8)
                events_dict[u_id] = {
                    "uid": u_id, "time": bj_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "tag": tag, "name": name, "title": title, "url": actual_url,
                    "manual": False
                }
            except: continue
        return events_dict

    # ---------------- 绘图方法（周表） ----------------
    def draw_card(self, draw, base_img, x, y, ev, fonts):
        COL_W = 260
        main_color = self.get_color(ev.get("url", ""))
        
        draw.rectangle([x - 10, y + 35, x - 6, y + 150], fill=main_color)
        draw.text((x, y), datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S").strftime('%H:%M'), fill=main_color, font=fonts['time'])
        
        title = ev["title"]
        if len(title) > 28: title = title[:27] + "..."
        lines = [title[i:i+8] for i in range(0, len(title), 8)][:3]
        
        card_h = 100 + (len(lines) - 1) * 25
        y_c = y + 35
        
        card_overlay = PILImage.new('RGBA', (COL_W - 30, card_h), (255, 255, 255, 180))
        base_img.paste(card_overlay, (x, y_c), card_overlay)
        
        draw.rounded_rectangle([x + 10, y_c + 15, x + 65, y_c + 40], radius=5, fill=main_color)
        draw.text((x + 16, y_c + 17), ev["tag"], fill="#FFFFFF", font=fonts['tag'])
        draw.text((x + 75, y_c + 16), ev["name"], fill=main_color, font=fonts['tag'])
        
        for i, line in enumerate(lines):
            draw.text((x + 12, y_c + 55 + i * 28), line, fill="#555555", font=fonts['title'])
            
        return card_h + 60

    # ---------------- 绘图方法（日表定制） ----------------
    def draw_today_card(self, draw, base_img, x, y, ev, fonts):
        CARD_W = 460
        main_color = self.get_color(ev.get("url", ""))
        
        # 绘制主时间
        time_str = datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S").strftime('%H:%M')
        draw.text((x, y), time_str, fill=main_color, font=fonts['time_lg'])
        
        title = ev["title"]
        # 日表可以显示更长的主题
        lines = [title[i:i+16] for i in range(0, len(title), 16)][:4] 
        
        card_h = 110 + (len(lines) - 1) * 32
        y_c = y + 45
        
        # 宽敞版的半透明卡片
        card_overlay = PILImage.new('RGBA', (CARD_W, card_h), (255, 255, 255, 210))
        base_img.paste(card_overlay, (x, y_c), card_overlay)
        
        # 左侧强调色条
        draw.rectangle([x, y_c, x + 8, y_c + card_h], fill=main_color)
        
        # Tag 与 Name
        draw.rounded_rectangle([x + 20, y_c + 20, x + 80, y_c + 50], radius=6, fill=main_color)
        draw.text((x + 28, y_c + 23), ev["tag"], fill="#FFFFFF", font=fonts['tag_lg'])
        draw.text((x + 95, y_c + 22), ev["name"], fill=main_color, font=fonts['name_lg'])
        
        # 标题多行渲染
        for i, line in enumerate(lines):
            draw.text((x + 20, y_c + 65 + i * 32), line, fill="#333333", font=fonts['title_lg'])
            
        return card_h + 80

    async def fetch_and_merge_events(self):
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(self.url, timeout=15)
                fetched_evs = self.parse_ics_to_dict(resp.text)
                all_evs = self.load_cached_events()
                for uid, ev in fetched_evs.items():
                    if uid not in all_evs or not all_evs[uid].get("manual"):
                        all_evs[uid] = ev
                self.save_events(all_evs)
                return sorted(all_evs.values(), key=lambda x: x["time"])
            except Exception as e:
                print(f"[asoul_calendar] 更新出错: {e}")
                return None

    async def update_calendar_image(self, week_offset=0):
        suffix = "this" if week_offset == 0 else "next"
        image_path = os.path.join(self.data_dir, f"combined_schedule_{suffix}.png")
        
        render_list = await self.fetch_and_merge_events()
        if not render_list: return None
            
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start_w = (today - timedelta(days=today.weekday())) + timedelta(weeks=week_offset)
        
        w_data = {i: [] for i in range(7)}
        for e in render_list:
            ev_dt = datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S")
            diff = (ev_dt.date() - start_w.date()).days
            if 0 <= diff <= 6: w_data[diff].append(e)

        CW, MT = 280, 200
        img_w = CW * 7 + 100
        img = PILImage.new('RGB', (img_w, 1200), color="#FFF5F7")
        draw = ImageDraw.Draw(img)
        
        try:
            fonts = {
                'header': ImageFont.truetype(self.font_path, 60),
                'date': ImageFont.truetype(self.font_path, 24),
                'time': ImageFont.truetype(self.font_path, 22),
                'tag': ImageFont.truetype(self.font_path, 18),
                'title': ImageFont.truetype(self.font_path, 22)
            }
        except: return None

        header_text = f"A-SOUL · {'本周' if week_offset == 0 else '下周'}日程"
        draw.text((50, 60), header_text, fill="#E799B0", font=fonts['header'])
        
        max_y = 0
        for i in range(7):
            x = 60 + i * CW
            curr_d = start_w + timedelta(days=i)
            d_clr = "#E799B0" if curr_d.date() == datetime.now().date() else "#888888"
            draw.text((x, 170), ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"][i], fill=d_clr, font=fonts['date'])
            draw.text((x + 65, 170), curr_d.strftime('%m.%d'), fill=d_clr, font=fonts['date'])
            y_o = MT + 40
            for ev in w_data[i]:
                y_o += self.draw_card(draw, img, x, y_o, ev, fonts)
            max_y = max(max_y, y_o)
        
        final_img = img.crop((0, 0, img_w, max(max_y + 100, 600)))
        final_img.save(image_path)
        return image_path

    async def update_today_image(self):
        image_path = os.path.join(self.data_dir, "schedule_today.png")
        render_list = await self.fetch_and_merge_events()
        if not render_list: return None
        
        today_date_str = datetime.now().strftime("%Y-%m-%d")
        today_events = [e for e in render_list if e["time"].startswith(today_date_str)]
        
        img_w = 600
        img = PILImage.new('RGB', (img_w, 1200), color="#FFF5F7")
        draw = ImageDraw.Draw(img)
        
        try:
            fonts = {
                'header': ImageFont.truetype(self.font_path, 56),
                'date': ImageFont.truetype(self.font_path, 28),
                'time_lg': ImageFont.truetype(self.font_path, 34),
                'tag_lg': ImageFont.truetype(self.font_path, 20),
                'name_lg': ImageFont.truetype(self.font_path, 24),
                'title_lg': ImageFont.truetype(self.font_path, 26)
            }
        except: return None

        # 头部设计
        draw.text((70, 60), "今日日程", fill="#E799B0", font=fonts['header'])
        draw.text((75, 135), f"{datetime.now().strftime('%Y年%m月%d日')}  " + ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][datetime.now().weekday()], fill="#888888", font=fonts['date'])
        
        y_o = 220
        if not today_events:
            draw.text((70, y_o), "今天没有安排直播哦，好好休息吧！", fill="#AAAAAA", font=fonts['title_lg'])
            y_o += 100
        else:
            for ev in today_events:
                y_o += self.draw_today_card(draw, img, 70, y_o, ev, fonts)
                
        final_img = img.crop((0, 0, img_w, max(y_o + 80, 500)))
        final_img.save(image_path)
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
    async def send_this_week(self, event: AstrMessageEvent):
        '''获取本周日程表。用法：/日程表'''
        target_path = os.path.join(self.data_dir, "combined_schedule_this.png")
        path = await self.update_calendar_image(0)
        if path: yield event.image_result(path)

    @filter.command("下周日程")
    async def send_next_week(self, event: AstrMessageEvent):
        '''获取下周日程表。用法：/下周日程'''
        target_path = os.path.join(self.data_dir, "combined_schedule_next.png")
        path = await self.update_calendar_image(1)
        if path: yield event.image_result(path)
        
    @filter.command("今日日程")
    async def send_today(self, event: AstrMessageEvent):
        '''获取今日日程表。用法：/今日日程'''
        target_path = os.path.join(self.data_dir, "schedule_today.png")
        path = await self.update_today_image()
        if path: yield event.image_result(path)

    @filter.command("更改日程")
    async def manage_calendar(self, event: AstrMessageEvent, action: str = None, idx: int = None, *args):
        '''
        管理日程。
        添加示例：/更改日程 添加 2026-04-14 20:00 节目 A-SOUL游戏室 猛兽派对之夜
        修改示例：/更改日程 更改 1 2026-04-14 21:00
        删除示例：/更改日程 删除 1
        '''
        all_evs = self.load_cached_events()
        sorted_list = sorted(all_evs.values(), key=lambda x: x["time"])
        
        if action is None:
            msg = "【当前日程列表】\n"
            for i, ev in enumerate(sorted_list):
                msg += f"{i}. [{ev['time'][5:16]}] {ev['name']}-{ev['title']}\n"
            msg += "\n回复 /更改日程 (添加/删除/更改) 进行操作"
            yield event.plain_result(msg)
            return

        try:
            if action == "删除" and idx is not None:
                target_uid = sorted_list[idx]['uid']
                del all_evs[target_uid]
                self.save_events(all_evs)
                yield event.plain_result(f"成功删除序号 {idx} 的日程。")
            
            elif action == "添加":
                # args[0]=YYYY-MM-DD, args[1]=HH:MM, args[2]=类型, args[3]=大标题, args[4:]=主题
                if len(args) < 4:
                    yield event.plain_result("参数不足！正确格式：/更改日程 添加 YYYY-MM-DD HH:MM 类型 大标题 主题")
                    return
                
                new_time = f"{idx} {args[0]}:00" if isinstance(idx, str) else f"{args[0]} {args[1]}:00"
                
                # 处理因 idx 被 AstrBot 误解析为 args[0] 的情况，保证鲁棒性
                actual_args = [idx] + list(args) if isinstance(idx, str) else args
                if isinstance(idx, str):
                    new_time = f"{actual_args[0]} {actual_args[1]}:00"
                    new_tag = actual_args[2]
                    new_name = actual_args[3]
                    new_title = " ".join(actual_args[4:])
                else:
                    new_time = f"{args[0]} {args[1]}:00"
                    new_tag = args[2]
                    new_name = args[3]
                    new_title = " ".join(args[4:])

                new_uid = f"manual_{datetime.now().timestamp()}"
                all_evs[new_uid] = {
                    "uid": new_uid, "time": new_time, "tag": new_tag,
                    "name": new_name, "title": new_title, "url": "", "manual": True
                }
                self.save_events(all_evs)
                yield event.plain_result("日程添加成功！")

            elif action == "更改" and idx is not None:
                target_uid = sorted_list[idx]['uid']
                new_time = f"{args[0]} {args[1]}:00"
                all_evs[target_uid]['time'] = new_time
                all_evs[target_uid]['manual'] = True
                self.save_events(all_evs)
                yield event.plain_result(f"成功更改序号 {idx} 的时间。")
            
            # 更新所有相关视图
            yield event.plain_result("正在重新生成图片缓存...")
            await self.update_calendar_image(0)
            await self.update_calendar_image(1)
            await self.update_today_image()
            yield event.plain_result("缓存已刷新！")
            
        except Exception as e:
            yield event.plain_result(f"操作失败，请检查格式：{str(e)}")
