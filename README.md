# Company Manager · 公司工作台

Kevin 与 5 位成员共用的手机优先公司管理应用。支持财务流水、项目进度、客户沟通笔记、待办、Google Drive 附件及逐条修改历史。

- 所有成员可操作财务；管理员负责创建、停用账号和重置密码。
- 6 个启用账号上限；首次登录须改密；停用和改密使旧会话失效。
- 收支支持 USD/CNY、美元折算、支付/入账状态及负责人。金额用十进制字符串持久化，不自动猜测汇率。
- 每次业务写入与操作者、修改前后内容在同一数据库事务中提交；并发编辑使用版本检查，冲突返回 409。
- 登录后数据不写入浏览器缓存；PWA 可添加到手机主屏幕，需联网使用。

## 本地运行

Python 3.13 或 3.14。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export ADMIN_INITIAL_PASSWORD='replace-with-a-long-random-password'
.venv/bin/uvicorn app:app --reload --port 8088
```

打开 `http://127.0.0.1:8088`，初始管理员账号为 `kevin`。首次启动需要至少 12 位初始密码，之后以数据库里的密码为准。本地数据保存在 `.local/app.sqlite`；不提交到版本控制。

```bash
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest -q
node --check static/app.js
```

## GCP 部署

Cloud Run + Cloud SQL PostgreSQL + Secret Manager。生产没有 `DATABASE_URL` 时拒绝启动，避免把业务数据误存到临时磁盘。不要给运行时数据库用户超级管理员权限。独立服务 `cmpmanager`，运行时账号 `cmpmanager-runtime`。

预先创建：

1. 独立 PostgreSQL 数据库及仅有该库 schema 使用/建表权限的登录角色。
2. `cmpmanager-database-url`：数据库 URL，使用 `/cloudsql/PROJECT:REGION:INSTANCE` Unix socket。
3. `cmpmanager-admin-password`：随机初始管理员密码。
4. `cmpmanager-runtime@PROJECT.iam.gserviceaccount.com`，授予 Cloud SQL Client，以及仅上述 secrets 的 Secret Accessor。

```bash
export GCP_PROJECT=your-project
export CLOUD_SQL_INSTANCE=your-project:us-central1:your-instance
export DRIVE_FOLDER_ID=your-google-drive-folder
./scripts/deploy.sh
```

脚本只部署 `cmpmanager`，使用 0–2 个实例；全部业务状态在 PostgreSQL 中。登录页面可公开访问，业务 API 必须登录。部署后验证 `/api/healthz`、`/api/me` 未登录返回 401、Cloud Run serving revision 和 SQL 持久化。对数据库使用 Cloud SQL 自动备份；不要将 `.local`、凭据或真实流水放入镜像。

## Google Drive 上传

直接上传需要用户 OAuth 授权。个人 My Drive 文件夹不能靠服务账号拥有新文件。配置三个环境变量，敏感值使用 Secret Manager：

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`（拥有目标文件夹上传权限的用户，授权包含 Drive scope）

接口把最多 20 MB 文件上传到 `DRIVE_FOLDER_ID`，并记录项目 ID、上传人及 Drive 链接。没有授权时明确显示“尚未连接”，支持先记录现有 Drive 文件链接。上传不会更改文件或文件夹的共享权限，因此访问链接仍须相应的 Google Drive 权限。

当前版本没有在线 OAuth 配置向导。管理员需要创建 Google OAuth 客户端并提供 refresh token，或者由运维人员帮助完成首次授权。聊天中的 Drive 连接不能直接作为部署应用的长期凭据。

## 已知边界

- 原 Google Sheet 作为字段参考和原表入口；无历史自动导入、自动同步或写回。
- 财务汇总是当前筛选的流水净额，含未付记录，不代表银行余额；未填写美元折算金额的人民币流水不纳入美元合计。
- 第一版不做离线写入、原生 App Store 打包、附件预览代理或自动给成员共享 Drive 文件。
- 无业务记录硬删除，项目用暂停或已完成状态管理。操作日志只通过服务接口追加；数据库管理人员仍具有数据库层面的管理能力。
- 状态接口一次返回全部业务记录；当前针对 6 人小团队。数据规模扩大后应增加分页和归档查询。
- 附件在 Drive 创建成功而数据库写入失败时，文件可能已保留在目标文件夹；可从文件夹查找并补录链接。

详细需求及设计记录见 [docs/product.md](docs/product.md)。
