# 枝江日程表

ASTRBOT 插件 —— 枝江直播日程表。数据来自 [asoul.love](https://asoul.love/calendar.ics)，通过 `/日程表` 指令生成并发送本周日程图片。

## 功能

- 每 6 小时自动从 `https://asoul.love/calendar.ics` 拉取最新日程
- `/日程表` 指令发送一张渲染好的周视图日程表图片

## 安装

1. 将整个 `枝江日程表` 目录放入 ASTRBOT 的 `addons/` 或插件目录
2. 下载字体文件 [msyh.ttf](https://github.com/awslYui/astrbot_plugin_asoul_calendar/raw/main/msyh.ttf) 放入插件目录；有安装微软雅黑的 Windows 系统可跳过
3. 安装依赖：`pip install httpx Pillow`
4. 重启 ASTRBOT

## 使用

在 QQ 群中发送：

```
/日程表
```

机器人将回复一张本周日程表图片，包含每日直播的时间、成员和标题。

## 依赖

- `httpx >= 0.25.0`
- `Pillow >= 10.0.0`

## 截图

![示例](https://github.com/awslYui/astrbot_plugin_asoul_calendar/raw/main/example.png)
