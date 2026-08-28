"""Deterministic, credential-free evaluation datasets.

The records are generated from fixed templates so the complete frozen dataset can be
reviewed as JSON without containing personal or production data.
"""

from __future__ import annotations

from typing import Any


def memory_dataset() -> dict[str, list[dict[str, Any]]]:
    cities = ["杭州", "苏州", "上海", "南京", "成都", "广州", "深圳", "北京"]
    styles = ["轻松慢游", "城市漫步", "自然风景", "博物馆", "亲子活动", "美食探店", "摄影", "短途徒步"]
    foods = ["粤菜", "川菜", "日料", "素食", "面食", "海鲜", "火锅", "本帮菜"]
    people = ["张明", "李岚", "王航", "陈雨", "赵宁", "周琦", "吴桐", "孙悦"]
    projects = ["星河", "云帆", "晨曦", "远航", "灯塔", "青鸟", "北斗", "山海"]
    deliverables = ["接口文档", "测试报告", "需求评审稿", "部署清单", "演示视频", "风险表", "周报", "验收材料"]
    meetings = ["架构评审", "产品周会", "项目复盘", "面试沟通", "需求澄清", "上线评审", "技术分享", "客户访谈"]
    locations = ["飞书会议", "腾讯会议", "滨江会议室", "西湖会议室", "北京A座", "上海B座", "线上Zoom", "科技园路演厅"]
    products = ["降噪耳机", "机械键盘", "显示器", "双肩包", "运动手表", "咖啡机", "行李箱", "人体工学椅"]
    brands = ["品牌甲", "品牌乙", "品牌丙", "品牌丁", "品牌戊", "品牌己", "品牌庚", "品牌辛"]

    memories: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []
    for i in range(40):
        city, style, food = cities[i % 8], styles[(i * 3) % 8], foods[(i * 5) % 8]
        item_id = f"conversation-{i:03d}"
        memories.append({
            "id": item_id,
            "type": "preference",
            "text": f"用户在第{i + 1}次对话中表示，去{city}时偏好{style}，餐饮优先{food}，不喜欢行程排得过满。",
            "summary": f"{city}旅行偏好：{style}、{food}",
            "source": "conversation",
            "tags": ["preference", "travel", city, style, food],
        })
        if i < 16:
            queries.append({
                "id": f"conversation-q-{i:02d}",
                "query": f"规划{city}行程时，我喜欢什么节奏和餐饮？",
                "relevant_ids": [item_id],
                "split": "dev" if i < 10 else "test",
            })

    for i in range(40):
        person, project, deliverable = people[i % 8], projects[(i * 3) % 8], deliverables[(i * 5) % 8]
        day = 10 + (i % 18)
        item_id = f"email-{i:03d}"
        memories.append({
            "id": item_id,
            "type": "email_fact",
            "text": f"{person}发送了{project}项目邮件，要求在2030年6月{day}日前提交{deliverable}，联系人邮箱为user{i}@example.com。",
            "summary": f"{project}项目{deliverable}截止日为6月{day}日",
            "source": "email",
            "tags": ["email", "deadline", project, deliverable],
        })
        if i < 16:
            queries.append({
                "id": f"email-q-{i:02d}",
                "query": f"{project}项目的{deliverable}由谁通知，截止到哪天？",
                "relevant_ids": [item_id],
                "split": "dev" if i < 10 else "test",
            })

    for i in range(40):
        meeting, location = meetings[i % 8], locations[(i * 3) % 8]
        day, hour = 1 + (i % 25), 9 + (i % 8)
        item_id = f"calendar-{i:03d}"
        memories.append({
            "id": item_id,
            "type": "calendar_event",
            "text": f"日程事件：2030年7月{day}日{hour}点举行{meeting}，地点为{location}，事件编号CAL-{i:03d}。",
            "summary": f"7月{day}日{meeting}@{location}",
            "source": "calendar",
            "tags": ["calendar", "schedule", meeting, location],
        })
        if i < 16:
            queries.append({
                "id": f"calendar-q-{i:02d}",
                "query": f"CAL-{i:03d}对应的{meeting}在什么地点、几点开始？",
                "relevant_ids": [item_id],
                "split": "dev" if i < 10 else "test",
            })

    for i in range(40):
        city, style = cities[(i * 5) % 8], styles[(i * 7) % 8]
        hotel = ["地铁旁酒店", "安静民宿", "景区内客栈", "商务酒店"][i % 4]
        item_id = f"travel-{i:03d}"
        memories.append({
            "id": item_id,
            "type": "travel_preference",
            "text": f"上次{city}旅行记录TRIP-{i:03d}显示，用户喜欢{style}，住宿选择{hotel}，每天最多安排三个主要活动。",
            "summary": f"{city}历史旅行：{style}、{hotel}",
            "source": "planning",
            "tags": ["travel", "plan", city, style, hotel],
        })
        if i < 16:
            queries.append({
                "id": f"travel-q-{i:02d}",
                "query": f"参考TRIP-{i:03d}，去{city}应该选择哪类住宿和行程密度？",
                "relevant_ids": [item_id],
                "split": "dev" if i < 10 else "test",
            })

    for i in range(40):
        product, brand = products[i % 8], brands[(i * 3) % 8]
        budget = 500 + i * 75
        item_id = f"shopping-{i:03d}"
        memories.append({
            "id": item_id,
            "type": "shopping_preference",
            "text": f"购物记录SHOP-{i:03d}：购买{product}时偏好{brand}，预算上限{budget}元，更看重耐用和售后。",
            "summary": f"{product}偏好{brand}，预算{budget}元",
            "source": "conversation",
            "tags": ["shopping", product, brand, str(budget)],
        })
        if i < 16:
            queries.append({
                "id": f"shopping-q-{i:02d}",
                "query": f"SHOP-{i:03d}里购买{product}的品牌偏好和预算是多少？",
                "relevant_ids": [item_id],
                "split": "dev" if i < 10 else "test",
            })

    assert len(memories) == 200
    assert len(queries) == 80
    return {"memories": memories, "queries": queries}


def routing_dataset() -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    dates = ["今天", "明天", "本周"]
    cities = ["杭州", "苏州", "上海", "南京", "成都"]
    tasks = ["回复邮件", "取快递", "买药", "项目会议", "健身"]
    risks = ["天气", "下雨", "交通", "堵车", "日程冲突"]
    general = [
        "解释一下Python装饰器", "帮我写一段项目介绍", "为什么天空是蓝色", "总结这段文字的观点", "给我三个产品名称",
        "如何学习异步编程", "比较REST和GraphQL", "写一个正则表达式", "介绍一下SQLite", "给代码补充注释",
    ]
    for i in range(30):
        split = "dev" if i < 20 else "test"
        date_word = dates[i % len(dates)]
        records.append({"id": f"calendar-{i:02d}", "text": f"请帮我查看{date_word}的飞书日程有哪些？", "label": "calendar_query", "split": split})
        records.append({"id": f"daily-{i:02d}", "text": f"{date_word}有{tasks[i % 5]}和会议，请帮我重新规划今天的安排", "label": "daily_plan", "split": split})
        records.append({"id": f"outing-{i:02d}", "text": f"帮我规划周末去{cities[i % 5]}旅行的轻松行程", "label": "outing_plan", "split": split})
        records.append({"id": f"smart-{i:02d}", "text": f"检查一下{date_word}{risks[i % 5]}会不会影响我的安排", "label": "smart_reminder", "split": split})
        records.append({"id": f"life-{i:02d}", "text": f"2030-09-{1 + i % 20:02d} {9 + i % 8}:00 项目同步会议，地点：飞书会议", "label": "life_event", "split": split})
        records.append({"id": f"general-{i:02d}", "text": general[i % len(general)] + f"，示例编号{i}", "label": "llm_slow", "split": split})
    assert len(records) == 180
    return records


def life_event_dataset() -> list[dict[str, Any]]:
    positive_templates = [
        ("interview", "2030-{month:02d}-{day:02d} {hour}:00 后端岗位面试，地点：腾讯会议，联系人：HR小李"),
        ("medical", "2030-{month:02d}-{day:02d} {hour}:00 预约医院体检，地点：市民中心医院"),
        ("flight", "2030-{month:02d}-{day:02d} {hour}:00 航班起飞，地点：萧山机场"),
        ("hotel", "2030-{month:02d}-{day:02d} {hour}:00 酒店入住，地点：西湖酒店"),
        ("bill", "2030-{month:02d}-{day:02d} {hour}:00 信用卡账单还款，金额：1288元"),
        ("delivery", "2030-{month:02d}-{day:02d} {hour}:00 快递取件，地点：滨江驿站"),
        ("meal", "2030-{month:02d}-{day:02d} {hour}:00 团队聚餐，地点：湖滨餐厅"),
        ("meeting", "2030-{month:02d}-{day:02d} {hour}:00 项目同步会议，地点：飞书会议"),
        ("personal_activity", "2030-{month:02d}-{day:02d} {hour}:00 我要去打羽毛球，地点：滨江球馆"),
    ]
    records: list[dict[str, Any]] = []
    index = 0
    for event_type, template in positive_templates:
        for i in range(10):
            month, day, hour = 1 + (index % 9), 1 + (i % 20), 9 + (i % 8)
            text = template.format(month=month, day=day, hour=hour)
            location = "未提供"
            if "地点：" in text:
                location = text.split("地点：", 1)[1].split("，", 1)[0]
            records.append({
                "id": f"positive-{index:03d}",
                "text": text,
                "is_event": True,
                "event_type": event_type,
                "start": f"2030-{month:02d}-{day:02d}T{hour:02d}:00:00",
                "location": location,
                "split": "dev" if i < 6 else "test",
            })
            index += 1

    negatives = [
        "请解释一下会议纪要应该怎么写", "我正在阅读航空发展史", "比较几家酒店的商业模式", "信用卡技术是如何工作的", "医院信息系统有哪些模块",
        "快递行业今年有什么趋势", "聚餐礼仪有哪些注意事项", "面试算法题应该怎么准备", "生成一个日历组件", "写一篇旅行随笔",
        "我不参加2030-10-01 10:00的会议", "取消考虑2030-10-02 11:00的酒店", "不要提醒我2030-10-03 12:00还信用卡", "我没有预约2030-10-04 13:00体检", "航班信息只是示例，不是我的行程",
        "这不是面试邀请，只是模板", "无需取件，快递已经退回", "聚餐已经取消，不需要记录", "会议已经删除，不要创建日程", "酒店订单已取消",
        "去年参加过一次项目会议", "以前住过西湖酒店", "上个月已经完成体检", "之前的航班已经结束", "过去的信用卡账单已还清",
        "曾经去快递站取过件", "历史面试记录仅供总结", "旧的聚餐照片需要整理", "过去入住酒店的发票", "去年医院检查结果正常",
    ]
    for i, text in enumerate(negatives):
        records.append({
            "id": f"negative-{i:03d}",
            "text": text,
            "is_event": False,
            "event_type": None,
            "start": None,
            "location": None,
            "split": "dev" if i < 18 else "test",
        })
    assert len(records) == 120
    return records


def planning_dataset() -> list[dict[str, str]]:
    destinations = ["杭州", "苏州", "上海", "南京", "成都", "广州", "深圳", "北京"]
    return [
        {
            "id": f"plan-{i:02d}",
            "destination": city,
            "kind": "trip" if i % 2 == 0 else "outing",
            "time_range": "2030-10-01 至 2030-10-03" if i % 2 == 0 else "2030-10-01",
            "budget": f"{1500 + i * 300}元",
            "companions": "两人" if i % 2 == 0 else "一人",
            "preferences": "轻松、少排队、有本地餐饮",
        }
        for i, city in enumerate(destinations)
    ]


def all_datasets() -> dict[str, Any]:
    return {
        "memory": memory_dataset(),
        "routing": routing_dataset(),
        "life_events": life_event_dataset(),
        "planning": planning_dataset(),
    }
