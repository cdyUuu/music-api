简体中文

<div align="center">

# 统一音乐 API

多平台音乐解析服务，纯 PHP + Python 实现

![GitHub Repo Size](https://img.shields.io/badge/repo-86KB-blue)
![License](https://img.shields.io/badge/license-MIT-green)

</div>

使用此项目导致的**封号**等情况**与本项目无关**

## 💡 功能支持性

- [x] 播放直链获取（kg / tx / wy / kw / migu）
- [x] 歌曲详情获取（kg / tx / wy / kw / migu）
- [x] 歌词获取（kg / tx / wy / kw / migu）
- [x] 凭证自动续期（kg / tx / migu）
- [x] 可视化配置后台
- [x] Python 常驻服务（Unix Socket，高并发低开销）
- [x] 失败自动重试与人机验证识别
- [x] API Key 鉴权与 CORS 白名单
- [x] 一键部署脚本

## 📦 支持平台与音质

| 平台 | source | 支持音质 |
|------|--------|---------|
| 酷狗 | kg | 128k / 320k / flac / flac24bit / hires |
| QQ音乐 | tx | 128k / 320k / flac / flac24bit / hires |
| 网易云 | wy | 128k / 320k / flac / flac24bit / hires |
| 酷我 | kw | 128k / 320k / flac / hires |
| 咪咕 | migu | 128k / 320k / flac / flac24bit |

## 💻 部署方法

环境要求：
- PHP 7.4+（cURL、OpenSSL、mbstring 扩展）
- Python 3.8+
- Nginx / Apache（一键脚本默认配置 Nginx）
- 推荐 Debian / Ubuntu 系

### 一键部署（推荐）

```bash
# 1. 获取代码（Git Clone 或下载 ZIP 解压均可）
git clone https://github.com/你的用户名/你的仓库名.git
cd 你的仓库名

# 2. 一键安装（需要 root）
sudo bash deploy.sh
```

脚本自动完成：系统依赖安装 → Nginx 配置 → 虚拟环境创建 → 依赖安装 → 权限修正 → 服务启动 → 健康检查。

部署结束会打印**配置后台地址**（含密钥）。

常用参数：

```bash
bash deploy.sh --yes          # 全自动，全部使用默认值
bash deploy.sh --init-only    # 跳过系统依赖，仅初始化项目
bash deploy.sh --port 8080    # 自定义端口
bash deploy.sh --help         # 查看全部参数
```

### 手动部署

```bash
# 1. 创建虚拟环境并安装依赖
python3 -m venv venv
venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 复制配置
cp config.json.example config.json

# 3. 生成管理密钥
php gen_key.php

# 4. 配置 Nginx（参考 deploy.sh 中的模板）
# 5. 启动 Python 常驻服务
systemctl enable --now music-api-py
```

## 🔌 接口说明

所有接口通过 `index.php` 访问。

### 播放直链

```
GET /index.php?source={平台}&songId={歌曲ID}&quality={音质}
```

### 歌曲信息

```
GET /index.php?action=info&source={平台}&songId={歌曲ID}
```

### 歌词

```
GET /index.php?action=lyric&source={平台}&songId={歌曲ID}
```

### 健康检查

```
GET /index.php?action=health
```

咪咕支持用 `name` + `singer` 搜索，或用 `songmid` 指定歌曲。

## ⚙️ 配置后台

部署完成后访问脚本末尾打印的地址：

```
http://服务器IP:48006/admin.php?key=你的密钥
```

可在页面中完成：

- 填写酷狗 / QQ / 网易云的 Cookie 与 Token
- 填写咪咕账号密码
- 立即刷新全部平台凭证
- 安全设置：API Key 鉴权、CORS 白名单、错误信息脱敏

## 🔄 凭证自动续期

酷狗、QQ、咪咕的登录态会过期。添加定时任务每 6 小时自动刷新：

```bash
0 */6 * * * curl -s http://127.0.0.1:48006/refresh_cron.php > /dev/null 2>&1
```

酷狗 / QQ 通过 refreshKey 自动续期，咪咕通过账号密码重新登录。

**关于人机验证**：当平台触发风控要求人机验证时，接口返回 403 并提示需在手机设备上登录对应账号完成验证。此类验证无法自动绕过，在官方 APP 中验证解除后服务即恢复正常。

## 🚀 Python 常驻服务

为降低高并发下的进程开销，Python 解析逻辑以 FastAPI + Uvicorn 常驻运行：

- Socket 路径：`/run/music-api/music-api.sock`
- systemd 服务：`music-api-py.service`（崩溃自动重启）
- PHP 优先走 Socket，服务不可用时自动回退到 `exec` 方式
- 健康检查：`curl --unix-socket /run/music-api/music-api.sock http://localhost/health`

## 📁 目录结构

```
├── index.php          # 统一入口（咪咕实现、鉴权、风控识别、失败重试）
├── admin.php          # 可视化配置后台
├── refresh_cron.php   # 全平台凭证刷新
├── migu_login.php     # 咪咕登录
├── migu_cron.php      # 咪咕单独刷新
├── gen_key.php        # 管理密钥生成
├── deploy.sh          # 一键部署脚本
├── config.json        # 配置文件（发布版不含，由脚本生成）
├── requirements.txt   # Python 依赖
├── py/                # Python 脚本与核心库
│   ├── server.py      # FastAPI 常驻服务
│   ├── kg_tx.py       # 酷狗/QQ CLI 入口
│   ├── wy.py          # 网易云
│   ├── kw_vip.py      # 酷我（多渠道聚合）
│   └── lx/            # 酷狗/QQ 核心模块（见下方致谢）
├── data/              # 凭证存储
├── logs/              # 日志
└── cache/             # 缓存
```

## 🔒 安全建议

- 对外暴露时在后台启用 **API Key 鉴权**
- 生产环境开启**错误信息脱敏**（不返回内部路径与调试信息）
- 部署脚本已配置 Nginx 禁止访问 `config.json`、`data/`、`logs/`、`venv/`、`py/` 等敏感路径
- 建议为站点配置 HTTPS

## 🙏 致谢

本项目的酷狗音乐、QQ音乐解析模块（`py/lx/` 目录）基于 [lx-music-api-server](https://github.com/MeoProject/lx-music-api-server) 的部分源码提取与适配，包括核心工具库、酷狗/QQ 的签名算法、播放直链、歌词解密与 Token 续期逻辑，在此致谢。

## 📄 项目协议

本项目基于 MIT 许可证发行。

本项目的数据来源是从各官方音乐平台的公开服务器中拉取数据，本项目不对数据的准确性负责。使用本项目产生的版权数据，请在 24 小时内清除。

本项目仅用于对技术可行性的探索及研究，禁止在违反当地法律法规的情况下使用。

音乐平台不易，请尊重版权，支持正版。
