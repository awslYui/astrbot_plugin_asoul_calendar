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
        self.font_path = os.path.join(os.path.dirname(__file__), "MiSans-Regular.ttf")
        
        # 关键字参数确保兼容性
        self.context.register_task(
            cron="0 */12 * * *", 
            func=self.update_calendar_image, 
            desc="A-SOUL 日程更新"
        )

    def parse_summary_v2(self, summary_text):
        """核心解析逻辑：拆分 Tag, 名字, 和 标题"""
        # 直播类型预定义
        types = ["突击", "2D", "日常", "节目"]
        found_tag = "日常" # 兜底类型
        
        # 1. 提取第一个 【】 内的内容作为直播类型和名字
        # 例子: 【突击】贝拉突击: 【突击】一起看前瞻
        match = re.search(r"^【(.*?)】(.*?)[:：]", summary_text)
        if match:
            type_and_name_part = match.group(1) # '突击'
            name_part = match.group(2) # '贝拉突击'
            full_title = summary_text.split(": ", 1)[1] if ": " in summary_text else summary_text
            
            # 确定直播类型
            for t in types:
                if t in type_and_name_part:
                    found_tag = t
                    break
                    
            return found_tag, name_part.strip(), full_title.strip()
        else:
            # 如果不符合 B站官方 ICS 格式，使用模糊识别
            for t in types:
                if t in summary_text:
                    found_tag = t
                    break
            return found_tag, "团播/夜谈", summary_text

    def parse_ics_advanced(self, text):
        events = []
        items = re.findall(r"BEGIN:VEVENT.*?END:VEVENT", text, re.S)
        for item in items:
            summary = re.search(r"SUMMARY:(.*)", item).group(1).strip() if re.search(r"SUMMARY:(.*)", item) else ""
            dtstart = re.search(r"DTSTART:(.*)", item).group(1).strip() if re.search(r"DTSTART:(.*)", item) else ""
            location = re.search(r"LOCATION:(.*)", item).group(1).strip() if re.search(r"LOCATION:(.*)", item) else ""
            
            if not dtstart or not summary: continue
            
            # 状态识别
            is_canceled = any(kw in summary for kw in ["取消", "延期", "更改"])
            
            # 深度解析 SUMMARY
            tag, name, title = self.parse_summary_v2(summary)
            
            try:
                t_str = dtstart[:16].replace('Z','')
                bj_dt = datetime.strptime(t_str, "%Y%m%dT%H%M%S") + timedelta(hours=8)
                events.append({
                    "time": bj_dt,
                    "tag": tag,
                    "name": name,
                    "title": title,
                    "url": location,
                    "canceled": is_canceled
                })
            except: continue
        return sorted(events, key=lambda x: x["time"])

    def get_color(self, url, title):
        group_color = "#5C6370" # 团播色
        mapping = {"22637261": "#E799B0", "22625027": "#576690", "22632424": "#DB7D74", "30849777": "#C93773", "30858592": "#7252C0"}
        for uid, color in mapping.items():
            if uid in url: return color
        kws = {"嘉然": "#E799B0", "贝拉": "#DB7D74", "乃琳": "#576690", "思诺": "#7252C0", "心宜": "#C93773"}
        for k, v in kws.items():
            if k in title: return v
        return group_color

    def draw_single_event(self, draw, x, y, ev, fonts):
        """精准手绘单个日程块，复刻例图排版"""
        COL_W = 280
        CARD_H = 110
        
        # 1. 绘制左侧灰色指示线
        draw.line([x - 15, y, x - 15, y + 140], fill="#DCDFE6", width=2)
        
        # 2. 时间文字
        time_color = "#99A2AA" if ev["canceled"] else "#666666"
        draw.text((x, y), ev["time"].strftime('%H:%M'), fill=time_color, font=fonts['time'])
        
        y_card = y + 30
        card_rect = [x, y_card, x + COL_W - 40, y_card + CARD_H]
        
        # 3. 卡片背景颜色
        main_color = "#E0E0E0" if ev["canceled"] else self.get_color(ev["url"], ev["title"])
        draw.rounded_rectangle(card_rect, radius=12, fill=main_color)
        
        # 4. 绘制 Tag 色块 (在卡片内部)
        tag_w = 60
        tag_h = 28
        tag_x = x + 15
        tag_y = y_card + 15
        # Tag 背景颜色（半透明白色，增加高级感）
        draw.rounded_rectangle([tag_x, tag_y, tag_x + tag_w, tag_y + tag_h], radius=6, fill="#FFFFFF44")
        # Tag 文字
        draw.text((tag_x + 10, tag_y + 3), ev["tag"], fill="#FFFFFF", font=fonts['tag'])
        
        # 5. 成员名字 (在 Tag 右侧)
        draw.text((tag_x + tag_w + 15, tag_y + 1), ev["name"], fill="#FFFFFF", font=fonts['main'])
        
        # 6. 直播标题 (在成员名字下方，支持简单换行)
        title_y = tag_y + 40
        title_txt = ev["title"]
        # 标题换行逻辑 (根据像素估算)
        line1, line2 = "", ""
        if len(title_txt) > 13:
            line1 = title_txt[:12]
            line2 = title_txt[12:25] + ("..." if len(title_txt) > 25 else "")
        else:
            line1 = title_txt
            
        draw.text((x + 15, title_y), line1, fill="#FFFFFF", font=fonts['title'])
        if line2:
            draw.text((x + 15, title_y + 25), line2, fill="#FFFFFF", font=fonts['title'])
            
        # 7. 如果取消，加删除线
        if ev["canceled"]:
            line_y_mid = y_card + CARD_H//2
            draw.line([x + 10, line_y_mid, x + COL_W - 50, line_y_mid], fill="#555555", width=3)
            
        return CARD_H + 50 # 返回该日程占用的高度

    async def update_calendar_image(self):
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(self.url, timeout=10)
                all_events = self.parse_ics_advanced(resp.text)
            except: return False
            
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start_of_week = today - timedelta(days=today.weekday())
            week_data = {i: [] for i in range(7)}
            for ev in all_events:
                diff = (ev["time"].date() - start_of_week.date()).days
                if 0 <= diff <= 6: week_data[diff].append(ev)

            # 画布参数：横排复刻例图比例
            COL_W, MARGIN_T = 300, 150
            max_ev = max([len(v) for v in week_data.values()] + [1])
            # 动态计算画布高度，日程多则长
            img_h = MARGIN_T + max_ev * 180 + 150
            
            # 使用淡蓝色底板，更接近 B站动态
            img = PILImage.new('RGB', (COL_W * 7 + 100, img_h), color="#F4F5F7")
            draw = ImageDraw.Draw(img)
            
            try:
                f_t = ImageFont.truetype(self.font_path, 42)
                f_d = ImageFont.truetype(self.font_path, 22)
                fonts = {
                    'time': ImageFont.truetype(self.font_path, 20),
                    'tag': ImageFont.truetype(self.font_path, 17),
                    'main': ImageFont.truetype(self.font_path, 20),
                    'title': ImageFont.truetype(self.font_path, 18),
                }
            except: return False

            draw.text((COL_W*3.5-40, 40), "本周日程", fill="#222222", font=f_t)
            w_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            
            for i in range(7):
                x = 60 + i * COL_W # MARGIN_L = 60
                curr_d = start_of_week + timedelta(days=i)
                # 绘制日期头
                is_today = (i == datetime.now().weekday())
                d_color = "#00AEEC" if is_today else "#666666"
                draw.text((x, 100), curr_d.strftime('%m月%d日'), fill=d_color, font=f_d)
                draw.text((x + 130, 100), w_names[i], fill=d_color, font=f_d)
                
                # 绘制今天高亮提示线
                if is_today:
                    draw.line([x - 10, 135, x + 200, 135], fill="#00AEEC", width=3)
                
                y_offset = MARGIN_T
                for ev in week_data[i]:
                    # 关键修改：调用精准绘图函数
                    used_h = self.draw_single_event(draw, x, y_offset, ev, fonts)
                    y_offset += used_h
            
            os.makedirs("data", exist_ok=True)
            img.save(self.image_path)
            return True

    @filter.command("日程表")
    async def send_calendar(self, event: AstrMessageEvent):
        if os.path.exists(self.image_path): yield event.image_result(self.image_path)
        else: yield event.plain_result("请先执行 /更新日程表")

    @filter.command("更新日程表")
    async def force_update(self, event: AstrMessageEvent):
        yield event.plain_result("正在同步 A-SOUL 日历并高精复刻排版...")
        if await self.update_calendar_image(): yield event.image_result(self.image_path)
        else: yield event.plain_result("同步失败，请检查字体文件。")
