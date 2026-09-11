# 部署记录

## 2026-09-10：私有附件与应用内预览

- 已发布 `cmpmanager-00013-f9q`，100% 流量；仅更新专属 `cmpmanager` 服务。
- 新附件使用 GCS 私有 bucket，位于 `us-central1`。启用 Uniform bucket-level access、Public access prevention 和 7 天 soft delete。后台仅获对象创建和读取权限。Google Drive OAuth 不再用于上传，已撤销后台对旧客户端凭据的读取权限。
- PDF 在应用内翻页和缩放；PNG/JPEG/GIF/WebP、UTF-8 文本直接预览，其他格式可下载。文件内容通过服务端授权接口提供；旧 Drive/Docs 链接继续在原网站打开。
- 132 项 Python 测试（含独立 PostgreSQL 非超级管理员/RLS 环境）和 14 项 JavaScript 测试通过。覆盖上传重试、原子审计、权限在网络读取中途被撤销、跨公司/个人空间隔离、伪造存储字段和内容完整性。
- 本机独立测试数据库结合真实 GCS 完成 PDF/图片/文本上传、重读和下载；下载原文件哈希一致。390 × 844 手机和 1280 × 900 桌面预览验收通过，无控制台错误。生产业务记录未加入测试数据。
- 线上健康检查和全部核心预览文件哈希一致，未登录上传/读取均返回 401；匿名直接访问 GCS 测试对象被拒绝。线上浏览器当前无登录会话，未使用生产账号新建测试项目。

## 历史部署记录

验证日期：2026-09-06（America/New_York）。

- GCP project：`iportfolio-497808`
- Cloud Run：`cmpmanager`，`us-central1`
- 应用地址：https://cmpmanager-uy5rniac4q-uc.a.run.app
- Cloud SQL 实例：`iportfolio-db`，PostgreSQL 16
- 独立数据库：`cmpmanager`
- 专属数据库角色：`cmpmanager_app`；无超级管理员、创建用户、创建数据库权限；已验证对 iPortfolio 业务表无 SELECT/INSERT/UPDATE/DELETE 权限。
- 专属服务账号：`cmpmanager-runtime`；Cloud SQL Client；仅 cmpManager 的两个 secrets 读取权限。
- 初始管理员：`kevin`。随机初始密码仅存 Secret Manager 与本机受保护 `.local` 文件。首次登录须改密。
- 生产业务记录：初始化为空；未导入参考财务表，未写入本地界面测试数据。

## 已完成验证

16 项自动化测试：登录/改密/会话失效、六人上限、成员财务权限、审计前后内容、版本冲突、金额和日期验证、引用完整性、跨站请求校验、持久登录限流、文件 URL 限制、Drive 上传 multipart 请求模拟、数据库重新打开。

本次分区功能新增 10 项测试：分区创建/改名、未分区旧记录、跨项目引用阻止、默认待办创建、已有待办关联与解除、关联任务更换项目保护、校验失败不创建待办，以及笔记和待办的原子回滚。

时间线功能验证：后端共 33 项测试通过，另有 2 项 JavaScript 测试验证日期排序、旧笔记兼容、分区筛选及颜色稳定性。本地手机界面实际验证默认勾选、取消并保存、同时创建待办、编辑后重新加入，以及不同分区颜色与筛选。所有界面验收记录仅存在本地测试数据库。

浏览器：390 × 844 手机尺寸下新建项目、进度显示、财务录入与金额汇总；测试过程无控制台 error/warn。测试数据只在 `.local/preview.sqlite` 中。

生产：健康检查通过；未登录 API 返回 401；初始账号登录成功，强制改密返回 403；Secure / HttpOnly cookie；退出后会话失效。独立 PostgreSQL 中 1 个初始管理员、0 条业务记录。

## 尚未完成

1. Google Drive 用户 OAuth 客户端与 refresh token 尚未配置，真实 PDF 直传未验收；当前可以添加 Drive 链接。聊天连接器读取文件成功，不代表部署服务已获长期授权。
2. 现有 Cloud SQL 实例 `backupConfiguration.enabled=false`。自动备份和时间点恢复尚未配置；本次未修改共享实例的备份策略。
3. 原 Google Sheet 历史数据未导入，当前不与原表同步。

这些状态应在配置改变后重新检查。
