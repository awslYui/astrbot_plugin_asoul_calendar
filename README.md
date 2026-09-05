# 枝江日程表

AstrBot 插件，用于生成适合 QQ 聊天窗口直接阅读的 A-SOUL 直播日程图片。日程数据来自 [asoul.love](https://asoul.love/calendar.ics)。

发送 `/日程表` 后，机器人会连续回复：

1. **今日日程**：1080 像素宽的大字单列卡片，突出直播时间、成员和标题。
2. **本周日程**：1200 像素宽的双列总览，今天高亮，已结束场次弱化显示。

## 功能

- 插件启动后立即更新，之后每 6 小时自动拉取一次日程。
- 执行 `/日程表` 时会再次尝试刷新数据。
- 将最新数据与本周缓存合并，保留已经结束且被上游移除的本周日程。
- 未来日程始终以上游最新数据为准，已取消的未来场次不会被旧缓存保留。
- 每周一自动淘汰上周的历史缓存。
- 网络请求失败时继续使用已有缓存。
- 根据日程数量动态计算图片高度，避免内容被截断。

## 安装

### AstrBot WebUI

在 AstrBot 管理面板的插件页面中，通过本仓库 URL 安装：

```
https://github.com/awslYui/astrbot_plugin_asoul_calendar
```

### 手动安装

将仓库目录放入：

```
data/plugins/astrbot_plugin_asoul_calendar/
```

然后安装依赖并重启 AstrBot：

```bash
pip install -r requirements.txt
```

仓库已经包含中文字体文件 `msyh.ttf`，通常不需要另外下载。

## 使用

在 AstrBot 支持的平台、群聊或私聊中发送：

```
/日程表
```

机器人会先发送今日日程，再发送本周日程。

## 缓存说明

缓存文件位于：

```
data/zhijiang_calendar/events_cache.json
```

由于 asoul.love 的 ICS 数据不包含已经过去的日程，插件只能保留其运行期间成功获取过的数据。如果在周中首次安装插件，安装前已经从上游消失的日程无法恢复；从首次成功拉取开始，本周日程会持续保留到周末。

删除缓存文件后，已经从上游消失的历史日程也会丢失。

## 依赖

- Python 3.10+
- AstrBot
- `httpx >= 0.25.0`
- `Pillow >= 10.0.0`

## 数据来源

日程由 [asoul.love/calendar.ics](https://asoul.love/calendar.ics) 提供。上游不可用且本地没有缓存时，插件会提示获取失败。
