# LLM API 网关

一个独立部署在服务器上的 **OpenAI 兼容计费网关**：对外只做一件事——卖 API 接入。
本地的「数学建模助教」app 不再内置任何支付逻辑，用户只要在它的「设置」里把
`Base URL` 和 `API Key` 换成本网关的，就能用本网关的额度。

```
本地 app (助教)                       服务器上的网关
LLM_BASE_URL = https://你的域名/v1   ──▶  /v1/chat/completions
LLM_API_KEY  = sk-网关生成的key            ├─ 验 key → 查余额
                                          ├─ 转发到上游 DeepSeek
                                          └─ 数 token → 扣费
                                     ──▶  /  /dashboard 网站
                                          登录 / 余额 / 卡密兑换 / Key 管理
```

## 本地启动

```bash
cd gateway
python -m venv .venv && .venv\Scripts\activate    # Windows
pip install -r requirements.txt
copy .env.example .env        # 填上 UPSTREAM_API_KEY 等
python -m gateway.app          # 或：uvicorn gateway.app:app --port 9000
```

> 注意：用 `python -m gateway.app` 时，工作目录要在 `gateway` 的**上一级**（revent/），
> 因为包名是 `gateway`。或直接 `cd gateway && uvicorn app:app`（此时把 `from .` 相对导入
> 当包跑：`cd revent && uvicorn gateway.app:app`）。推荐在 revent/ 下跑 `uvicorn gateway.app:app`。

打开 http://localhost:9000 注册账号 → 进控制台生成 API Key。

## 给用户充值（接在线支付前）

两种方式，都需要管理员口令（`.env` 里的 `ADMIN_TOKEN`，放在请求头 `X-Admin-Token`）：

**1. 直接给某账号充值**
```bash
curl -X POST http://localhost:9000/api/admin/recharge \
  -H "X-Admin-Token: 你的口令" -H "Content-Type: application/json" \
  -d '{"phone":"13800000000","amount_cents":1000}'   # 充 10 元
```

**2. 批量生成卡密**（用户在控制台自助兑换）
```bash
curl -X POST http://localhost:9000/api/admin/cards \
  -H "X-Admin-Token: 你的口令" -H "Content-Type: application/json" \
  -d '{"amount_cents":1000,"count":10}'   # 10 张 10 元卡密
```

## 计费口径

```
成本(元) = 输入token/1e6 × 输入价 + 输出token/1e6 × 输出价
售价(分) = ceil(成本 × BILLING_MARKUP × 100)
```
价格表在 `config.py` 的 `MODEL_PRICES`。**上线前务必核对上游官网真实价格。**
扣费先耗免费额度，再扣余额；余额可被扣成负（先用后扣），下次调用前余额≤0 会被拦。

## 公网部署红线

- `.env` 里 `AUTH_SECRET` / `ADMIN_TOKEN` 改成强随机值（`openssl rand -hex 32`）。
- 必须走 HTTPS（用 Nginx/Caddy 反代加证书），否则 key/密码明文可被截获。
- 登录、注册接口建议在反代层加频率限制（防爆破/刷注册）。
- `UPSTREAM_API_KEY` 只存在服务器，绝不出现在任何下发给用户的响应里。

## 下一步（未做）

接在线支付（虎皮椒 / PayJS）：下单 + 回调验签 + 自动到账。
表结构（`recharge_log.out_trade_no`、`status`）已为此预留，只需加一个回调端点调
`billing.recharge(...)`。
