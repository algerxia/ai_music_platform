PLANS = [
    {
        "code": "free",
        "name": "Free",
        "price_display": "¥0",
        "period": "once",
        "credits": 20,
        "purchasable": False,
        "commercial": False,
        "concurrency": 1,
        "features": ["一次性试用额度", "标准 MP3 生成", "非商用（需以供应商条款为准）"],
        "note": "本地演示默认跳过 Free，管理员走 Creator。",
    },
    {
        "code": "creator",
        "name": "Creator",
        "price_display": "示例 $19/月",
        "period": "month",
        "credits": 200,
        "purchasable": False,
        "commercial": True,
        "concurrency": 2,
        "features": ["每月示例 200 积分", "项目历史与版本", "变体迭代", "元数据导出"],
        "note": "支付未接入，本环境发放演示额度，不可真实扣款。",
    },
    {
        "code": "studio",
        "name": "Studio",
        "price_display": "示例 $49/月",
        "period": "month",
        "credits": 1000,
        "purchasable": False,
        "commercial": True,
        "concurrency": 4,
        "features": ["更高并发（规划）", "优先队列（规划）", "更多格式（仅在模型支持时）"],
        "note": "未开放购买，不暗示 MIDI/分轨已可用。",
    },
    {
        "code": "team",
        "name": "Team",
        "price_display": "示例 $149/月起",
        "period": "month",
        "credits": 3000,
        "purchasable": False,
        "commercial": True,
        "concurrency": 8,
        "features": ["多席位（未实现）", "角色权限（未实现）", "集中账单（未实现）"],
        "note": "P1 能力，页面标明候补。",
    },
]


def get_plan(code: str) -> dict:
    for plan in PLANS:
        if plan["code"] == code:
            return plan
    return PLANS[1]
