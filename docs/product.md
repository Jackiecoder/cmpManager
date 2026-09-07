# cmpManager 第一版

Kevin 与另外 5 位成员共同使用的公司工作台。所有成员可查看、新增和修改财务、项目、沟通笔记和待办。Kevin 管理账号；首次登录必须修改初始密码。每次业务更新将操作者、时间及修改前后内容写入审计日志。

## 界面设计

以手机日常录入为主，桌面展开为侧栏与内容区。参考 iPortfolio2 的主屏幕 Web App、清晰数字与安全区域适配。使用纸白 #FFFFFF、雾蓝 #F3F6FA、墨蓝 #18324C、湖蓝 #176B91、绿色 #197654、朱红 #BE4949。中文采用系统苹方字体，英文采用系统人文无衬线字体；正文 15px，标题 28px。信息左对齐，金额右对齐。

手机结构：顶部当前页面与新增动作；中部列表或项目记录；底部工作台 / 项目 / 财务 / 待办。项目详情按进度、沟通、待办、附件组织。工作台突出团队最近发生的工作，避免把每个功能都塞进相同的统计卡片。

## 数据

财务字段参考用户提供的《收支记账本》→《商会流水表》：日期、项目、事件、金额、人民币支付、转化美元、收支、支付状态、付款形式、入账状态、入账金额、负责人、备注。系统以原币金额 + 币种 + 手工确认的美元折算金额表达多币种；未知汇率不自行推算。余额为已录入收支净额，不等同于银行余额。原表仅作为字段参考，第一版不自动导入历史流水或改写原表。

项目包含负责人、状态、进度、期限及说明。笔记记录沟通日期、联系人、正文；任务包含责任人、截止日、完成状态；附件存入指定 Drive 文件夹，并关联所属项目及上传人。

## 部署与边界

FastAPI + 原生 HTML/CSS/JS；本地 SQLite，生产强制 PostgreSQL。GCP Cloud Run 使用独立 cmpmanager 服务、独立数据库与数据库用户，可复用 iPortfolio2 所在 Cloud SQL 实例。真实业务数据、初始密码及 OAuth 凭据不进入 GitHub 或容器镜像。

Google Drive 个人云盘上传通过用户 OAuth refresh token，配置在 Secret Manager。未连接时明确提示，可以记录已有 Drive 文件链接。Google 文档说明个人云盘应使用用户 OAuth，服务账号不能拥有文件：https://developers.google.com/workspace/drive/api/guides/about-shareddrives 。Cloud Run 与 SQL 通过专属服务账号、Cloud SQL Client 和 Unix socket 连接：https://docs.cloud.google.com/sql/docs/postgres/connect-run 。

第一版是需联网的 PWA，无离线财务写入。成员姓名由管理员实际创建，不预填虚构人员。无业务记录硬删除接口；项目用暂停/完成归档状态。附件链接沿用原 Drive 权限。
