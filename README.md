# vaxport

疫苗企业本地 LLM 数据分析终端工具 — 用自然语言查询 PostgreSQL 数据库。

## 启动

```bash
# 直接启动 Textual TUI
make dev

# 或
vaxport

# 直接查询（非交互模式）
vaxport "查询所有批次的质量数据"
```

## 前置要求

### Python 3.12+

按操作系统选择安装方式：

**Ubuntu / Debian**

```bash
sudo apt update && sudo apt install python3.12 python3.12-venv python3-pip -y
```

**macOS**

```bash
brew install python@3.12
```

**Windows**

从 [python.org](https://www.python.org/downloads/) 下载 Python 3.12 安装包。安装时务必勾选 **Add Python to PATH**。

验证安装：

```bash
python3 --version   # 应显示 Python 3.12.x
```

> **注意**：Windows 用户如果命令行中 `python3` 不识别，改用 `python`。后续所有命令同理。

### Git

用于克隆项目代码。Linux/macOS 通常自带，Windows 从 [git-scm.com](https://git-scm.com/) 下载。

### 网络

- 连接 PostgreSQL 服务器（通常在公司内网）
- 连接阿里百炼 API（`dashscope.aliyuncs.com`）

---

## 安装

### 方式一：Git 克隆（推荐）

```bash
git clone <repo-url> && cd vaxport
pip install -e .
```

### 方式二：离线安装（无需 Git）

1. 让同事把 `vaxport` 文件夹打包发给你（zip/tar），或通过 U 盘/共享目录拷贝
2. 解压后进入目录：

```bash
unzip vaxport.zip -d vaxport    # Linux/macOS
# 或在文件管理器中右键解压（Windows）
```

3. 打开终端，进入解压后的目录：

```bash
cd vaxport
pip install -e .
```

依赖包会自动安装（psycopg2、textual、matplotlib 等 9 个），无需额外安装系统库。

---

## 配置准备

启动 vaxport 前需要准备 **两项信息**：PostgreSQL 连接信息和 API Key。

### 一、获取 PostgreSQL 连接信息

向数据库管理员（DBA）索取以下 5 项信息：

| 信息 | 示例 | 说明 |
|------|------|------|
| **主机地址** | `10.21.134.109` | PostgreSQL 服务器的 IP 或域名 |
| **端口** | `5432` | 通常是 5432 |
| **数据库名** | `myappdb` | 业务数据库名称 |
| **用户名** | `vlm_reader` | 只读查询账号 |
| **密码** | `****` | 账号密码 |

### 二、本机 PostgreSQL 配置（服务器端）

如果 PostgreSQL 安装在本机，按以下步骤配置：

**1. 安装 PostgreSQL**

```bash
sudo apt update && sudo apt install postgresql postgresql-client -y
```

**2. 导入测试数据**

项目根目录下的 `myappdb_full.dump`（~556KB）是疫苗企业模拟业务数据库的完整 PostgreSQL dump，包含 7 个产品线（PEDV/ECOLI/APP/PRRSV/HPS/SS/HPSSS_COMBO）的生产、质量、冷链、仓储、设备、人力、药物警戒共 7 个 schema 的模拟数据。

```bash
# 创建空库
sudo -u postgres createdb myappdb

# 导入 dump（在项目目录下执行）
cd vaxport
sudo -u postgres pg_restore -d myappdb -j 4 myappdb_full.dump
```

**3. 创建只读用户**

```bash
sudo -u postgres psql -d myappdb
```

```sql
CREATE ROLE vlm_reader WITH LOGIN PASSWORD 'your_password';
GRANT CONNECT ON DATABASE myappdb TO vlm_reader;
GRANT USAGE ON SCHEMA
    analog_production, analog_quality, analog_coldchain,
    analog_warehouse, analog_equipment, analog_hr, analog_pv
TO vlm_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA
    analog_production, analog_quality, analog_coldchain,
    analog_warehouse, analog_equipment, analog_hr, analog_pv
TO vlm_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA
    analog_production, analog_quality, analog_coldchain,
    analog_warehouse, analog_equipment, analog_hr, analog_pv
GRANT SELECT ON TABLES TO vlm_reader;
```

**4. 开放网络访问（允许客户端远程连接）**

编辑 `pg_hba.conf`（路径可通过 `sudo -u postgres psql -c "SHOW hba_file;"` 查看）：

```
# 添加客户端 IP 网段（示例：允许 10.21.128.0/21 网段）
host    all    vlm_reader    10.21.128.0/21    scram-sha-256
```

编辑 `postgresql.conf`：

```
listen_addresses = '*'
```

重启 PostgreSQL：

```bash
sudo systemctl restart postgresql
```

**5. 放行防火墙**

```bash
sudo ufw allow 5432/tcp
```

**6. 验证本机连接**

```bash
psql -h localhost -p 5432 -U vlm_reader -d myappdb -c "SELECT 1;"
```

### 三、客户端连接本机 PostgreSQL（同一局域网）

> **注意**：以下 IP `10.21.134.109` 是服务器在**局域网内的私有地址**，仅限同一局域网（同一路由器/交换机下）的客户端使用。如果客户端与服务器不在同一网络（如远程办公、出差），请先参考 [跨网络连接](#三-b-跨网络连接不同局域网) 建立隧道。

客户端电脑无需安装 PostgreSQL 服务端，只需按以下步骤配置：

**1. 确认网络可达**

本机局域网 IP：`10.21.134.109`

```bash
ping 10.21.134.109
```

能 ping 通说明在同一局域网，可直接使用此 IP。

> **如何查看服务器 IP**：在服务器上执行 `hostname -I` 或 `ip addr show`，找到局域网 IP（通常以 `192.168.` 或 `10.` 开头）。

**2. 首次启动 vaxport 时填写配置**

| 提示 | 填写 |
|------|------|
| `PG 主机` | 服务器 IP 地址（如 `10.21.134.109`） |
| `PG 端口` | `5432` |
| `PG 数据库` | `myappdb` |
| `PG 用户` | `vlm_reader` |

或手动编辑 `~/.vaxport/config.yaml`：

```yaml
pg:
  host: 10.21.134.109    # 服务器 IP
  port: 5432
  database: myappdb
  user: vlm_reader
```

**3. 验证连接**

```bash
psql -h 10.21.134.109 -p 5432 -U vlm_reader -d myappdb -c "SELECT 1;"
```

> **注意**：客户端机器不需要安装完整 PostgreSQL，只需 `postgresql-client` 用于验证。vaxport 本身通过 psycopg2 直连，不依赖 psql 客户端。

### 三-B、跨网络连接（不同局域网）

如果客户端与服务器**不在同一局域网**（如在家办公、出差），`10.21.134.109` 是私有 IP，公网不可达。需要先建立网络隧道，让客户端能触达服务器。以下四种方案按推荐度排列：

#### 方案一：Tailscale（推荐，零配置，免费）

最简单的点对点 VPN，基于 WireGuard，免费版支持最多 100 台设备。不需要公网 IP，不需要改防火墙，两台设备装上就能互通。

**原理**：Tailscale 在两台设备间建立加密的点对点隧道，分配 `100.x.x.x` 格式的虚拟 IP，之后两台设备通过虚拟 IP 互访，就像在同一局域网一样。

##### 第一步：注册 Tailscale 账号

用 GitHub / Google / Microsoft 账号在 [tailscale.com](https://login.tailscale.com/start) 免费注册。

##### 第二步：服务器端（Ubuntu）安装

在 Ubuntu 服务器上执行：

```bash
# 安装 Tailscale
curl -fsSL https://tailscale.com/install.sh | sh

# 启动并登录（会打印一个 URL，在浏览器打开完成认证）
sudo tailscale up
```

验证安装成功：

```bash
tailscale ip -4   # 应显示 100.x.x.x 格式的 IP
tailscale status  # 应显示本机 online
```

##### 第三步：客户端（macOS）安装

```bash
# 方式一：Homebrew（推荐）
brew install tailscale
tailscale up

# 方式二：App Store 安装
# 搜索 "Tailscale"，安装后打开并登录
```

> **Windows 客户端**：从 [tailscale.com/download](https://tailscale.com/download) 下载安装包。

验证安装成功：

```bash
tailscale ip -4   # 应显示 100.x.x.x
```

##### 第四步：验证互通

在 Mac 上 ping 服务器的 Tailscale IP：

```bash
# 把 <服务器 Tailscale IP> 替换为第二步中 tailscale ip -4 显示的值
ping <服务器 Tailscale IP>
```

能 ping 通说明隧道建立成功。

##### 第五步：配置 vaxport

编辑 `~/.vaxport/config.yaml`，如果启用了 SSH 隧道，先关闭：

```yaml
pg:
  host: <服务器 Tailscale IP>   # 如 100.123.45.67
  port: 5432
  ssh_tunnel:
    enabled: false              # 不需要 SSH 隧道了
```

重启 vaxport 即可正常连接数据库。

##### 关停

不需要时，在服务器上执行：

```bash
sudo tailscale down   # 断开连接
```

下次需要时再 `sudo tailscale up` 即可。

> **安全提示**：Tailscale 使用 WireGuard 端到端加密，流量不经过 Tailscale 服务器。仅在建立连接时需要 Tailscale 协调服务器做 NAT 穿透，数据传输是点对点的。

#### 方案二：SSH 隧道

适用于客户端可通过 SSH 登录服务器（或跳板机）的场景。

```bash
# 在客户端执行：将本地 5432 端口转发到服务器的 PostgreSQL
ssh -L 5432:localhost:5432 -N user@<服务器公网IP或跳板机>
```

然后 vaxport 配置中 `PG 主机` 填 `localhost`。

#### 方案三：frp 内网穿透

适用于服务器无公网 IP、但有一台有公网 IP 的 VPS 做中转。

- 公网 VPS 部署 `frps`（服务端）
- 内网服务器部署 `frpc`（客户端），将 `5432` 端口映射到 VPS 的某个端口

vaxport 配置中 `PG 主机` 填 VPS 的公网 IP，端口填映射端口。

#### 方案四：公司 VPN

如果公司已部署 VPN（如 OpenVPN、WireGuard、IPSec），客户端拨入 VPN 后即与服务器处于同一虚拟局域网，直接使用局域网 IP `10.21.134.109` 即可。

---

> **总结**：无论哪种方案，目标都是让客户端网络层能触达 PostgreSQL 的 5432 端口。隧道建立后，vaxport 配置中的 `PG 主机` 填隧道对端地址即可，其余配置（端口/数据库/用户）不变。

### 四、获取 API Key

vaxport 使用阿里百炼大模型，需要 API Key：

1. 访问 [阿里百炼控制台](https://bailian.console.aliyun.com)
2. 开通模型服务（DeepSeek-v4-flash、text-embedding-v4、qwen-vl-max）
3. 在 **API Key 管理** 页面创建 Key

拿到 Key 后，推荐写入环境变量（避免明文存在配置文件）：

```bash
# 追加到 ~/.bashrc 或 ~/.zshrc
export DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx
```

> **Windows 用户**：在"系统属性 → 环境变量"中添加 `DASHSCOPE_API_KEY`。

### 五、配置本地大模型（可选）

如果希望断网时仍可使用，可在自己电脑上安装 Ollama 并下载模型：

```bash
# 安装 Ollama: https://ollama.com
ollama pull qwen3:14b
```

不配置也不影响正常使用（云端 API 可用时优先走云端）。

---

## 首次启动

```bash
vaxport
```

首次运行会自动弹出配置引导，依次输入：

| 提示 | 填什么 |
|------|--------|
| `API Key (阿里云百炼)` | 如果已设环境变量则留空直接回车 |
| `PG 主机` | 填 DBA 给的主机地址（本机填 `localhost`） |
| `PG 端口` | 默认 `5432`，直接回车 |
| `PG 数据库` | 填 DBA 给的数据库名 |
| `PG 用户` | 填 DBA 给的用户名 |
| `Ollama URL` | 默认 `http://localhost:11434`，没装则回车跳过 |
| `本地模型名称` | 如装了 Ollama 则填模型名，否则留空回车 |

配置保存在 `~/.vaxport/config.yaml`，后续可手动修改。

---

## 验证安装

启动后看到欢迎界面即表示成功：

```
# 疫苗企业数据分析终端
- 模型: deepseek-v4-flash @ 阿里百炼
- 数据库: myappdb@10.21.134.109
- 4 个专家 (📊分析报告 · ⚖️质量监督 · 🔍文档检索 · 🤖通用)
```

尝试第一个查询：

```
▸ 显示所有表
```

如果返回数据库中的表列表，说明一切正常，可以开始使用了。

---

## 快速开始

### 导入测试数据（必需）

项目包含完整的疫苗生产模拟数据库 dump 文件（`myappdb_full.dump`），覆盖 7 个业务 schema、~40 张表、数千行数据。

#### 第一步：安装 PostgreSQL

**Ubuntu / Debian**

```bash
sudo apt update && sudo apt install postgresql postgresql-client -y
```

安装完成后 PostgreSQL 服务自动启动。验证：

```bash
sudo -u postgres psql -c "SELECT version();"
# 应显示: PostgreSQL 16.x / 17.x / 18.x ...
```

**macOS**

```bash
brew install postgresql@16
brew services start postgresql@16
```

> 如系统 Python 版本 ≥ 3.13，建议安装 PostgreSQL 17+。

**Windows**

从 [postgresql.org](https://www.postgresql.org/download/windows/) 下载安装包。安装过程中：
- 记住设置的 `postgres` 超级用户密码
- 端口保持默认 `5432`
- 勾选安装 **pgAdmin**（图形界面管理工具，可选）

#### 第二步：创建数据库并导入 dump

```bash
# 1. 创建空库
sudo -u postgres createdb myappdb

# 2. 导入数据（在项目目录下执行）
cd vaxport
sudo -u postgres pg_restore -d myappdb -j 4 myappdb_full.dump
```

`-j 4` 使用 4 线程并行导入，一般数秒完成。

> **Windows 用户**：如果 `sudo` 不可用，以管理员身份打开 PowerShell，替换命令为：
> ```powershell
> createdb -U postgres myappdb
> pg_restore -U postgres -d myappdb -j 4 myappdb_full.dump
> ```
> 输入安装时设置的 postgres 密码。

#### 第三步：创建只读用户

```bash
sudo -u postgres psql -d myappdb
```

在 psql 中执行：

```sql
-- 创建只读用户（修改密码）
CREATE ROLE vlm_reader WITH LOGIN PASSWORD 'your_password';

-- 授权连接
GRANT CONNECT ON DATABASE myappdb TO vlm_reader;

-- 授权所有 schema（共 7 个业务 schema）
GRANT USAGE ON SCHEMA
    analog_production, analog_quality, analog_coldchain,
    analog_warehouse, analog_equipment, analog_hr, analog_pv
TO vlm_reader;

GRANT SELECT ON ALL TABLES IN SCHEMA
    analog_production, analog_quality, analog_coldchain,
    analog_warehouse, analog_equipment, analog_hr, analog_pv
TO vlm_reader;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA
    analog_production, analog_quality, analog_coldchain,
    analog_warehouse, analog_equipment, analog_hr, analog_pv
GRANT SELECT ON TABLES TO vlm_reader;
```

> **Windows 用户**：`psql -U postgres -d myappdb`，输入密码后执行同样 SQL。

退出 psql：

```
\q
```

#### 第四步：验证

```bash
psql -h localhost -U vlm_reader -d myappdb -c "SELECT table_name FROM information_schema.tables WHERE table_schema='analog_production' LIMIT 5;"
```

输入创建的密码，应列出 `analog_production` 中的表。

#### 第五步：配置 vaxport

数据库准备就绪后，首次启动 `vaxport` 时，配置引导中填写：

| 提示 | 填写 |
|------|------|
| `PG 主机` | `localhost` |
| `PG 端口` | `5432` |
| `PG 数据库` | `myappdb` |
| `PG 用户` | `vlm_reader` |

> **API Key**：仍需配置阿里百炼 API Key，参见[配置准备](#配置准备-二获取-api-key)一节。

---

<details>
<summary><b>补充：用 Python 脚本生成更多模拟数据（可选）</b></summary>

项目还内置了 PEDV 灭活疫苗数据生成器，可在 `myappdb` 基础上追加更多数据：

| 环节 | 表数 | 说明 |
|------|------|------|
| 上游生产 | 8 | 细胞/病毒培养、收获灭活、中间品、QC |
| 下游冷链 | 3 | 成品储存温度、运输监控、仓储环境 |
| 供应链 | 5 | 物料库存(~40种)、BOM消耗、入厂检验、仓储异常 |
| 质量体系 | 4 | 偏差(16 异常)、仪器校准、人员培训、AEFI |
| 洁净区 | 2 | 环境监测、仓储温湿度 |

```bash
# 追加 PEDV 数据（会创建新 schema analog_pedv）
PGPASSWORD=your_password python scripts/generate_pedv_data.py \
    --user postgres --password your_password
```

生成后 schema `analog_pedv` 下有 ~4,800 行数据。

</details>

### 交互模式

```bash
vaxport
```

进入 TUI 后直接输入问题：

```
▸ 最近 10 批 PEDV 疫苗的效价趋势如何？
```

### 一次性查询

```bash
vaxport "查批次 PEDV-2024-0187 的效价"
```

### 会话管理

```bash
vaxport --list-sessions      # 列出已保存会话
vaxport --resume 2026-05-22  # 恢复指定会话
```

---

## 常见问题

### 启动后显示"数据库未连接"

首先确认网络可达性：

```bash
ping 10.21.134.109
```

- **能 ping 通**：检查 PG 主机地址和端口是否正确
- **不能 ping 通**：
  - 同一局域网：检查服务器防火墙、PG 服务是否启动
  - 不同局域网：`10.21.134.109` 是局域网私有 IP，公网不可达。请参考 [跨网络连接](#三-b-跨网络连接不同局域网) 先建立隧道

### `FATAL: password authentication failed`

用户名或密码不对，联系 DBA 确认账号信息。

### `FATAL: no pg_hba.conf entry for host`

DBA 尚未授权你的 IP 访问 PG，请 DBA 在 `pg_hba.conf` 中添加你的 IP 网段。

### `Connection timed out`

可能原因：
- 服务器防火墙未放行 5432 端口
- 客户端 IP 不在 `pg_hba.conf` 允许范围内
- **跨网络未建立隧道**：如果客户端与服务器不在同一局域网，`10.21.134.109` 不可达，请参考 [跨网络连接](#三-b-跨网络连接不同局域网)

### 安装时 `pip: command not found`

Python 未正确安装或未加入 PATH。重新运行 Python 安装包并勾选"Add to PATH"，或使用 `python3 -m pip install -e .` 替代。

### `ModuleNotFoundError: No module named 'vaxport'`

安装未完成，重新执行：

```bash
cd vaxport && pip install -e .
```

## PostgreSQL 计算引擎能力

遵循 **SQL 是计算引擎，LLM 是交互界面** 的原则，所有数值计算在 PostgreSQL 内完成。

### 内置聚合函数

| 函数 | 用途 | 疫苗企业场景 |
|------|------|-------------|
| `AVG(col)` | 均值 | 批次效价均值、环境温湿度均值 |
| `STDDEV(col)` | 标准差 | 效价波动、工艺参数离散度 |
| `PERCENTILE_CONT(0.5)` | 中位数 | 效价中位数 |
| `MIN(col)` / `MAX(col)` | 最值 | 效价极值、温度峰值 |

### 内置统计函数

| 函数 | 用途 | 疫苗企业场景 |
|------|------|-------------|
| `CORR(x, y)` | Pearson 相关系数 | 温度 vs 效价相关性 |
| `REGR_SLOPE(y, x)` | 线性回归斜率 | 效价趋势（每批变化量） |
| `REGR_R2(y, x)` | 拟合度 R² | 趋势可靠性 |
| `PERCENTILE_CONT(0.25)` / `(0.75)` | 四分位数 | IQR 离群值检测 |

### 内置窗口函数

| 函数 | 用途 | 疫苗企业场景 |
|------|------|-------------|
| `LAG(col, n) OVER (...)` | 前 n 行值 | 环比变化（本月 vs 上月） |
| `ROW_NUMBER() OVER (...)` | 行号 | 分组 Top-N |
| `RANK() OVER (...)` | 排名 | 供应商排名 |

### PG 扩展函数（plpython3u）

| 函数 | 用途 | 输出 |
|------|------|------|
| `calc_cpk(vals, usl, lsl)` | 过程能力指数 | n, mean, std, cp, cpk, judgment |
| `t_test_welch(a, b)` | Welch t 检验 | t_stat, p_value, cohens_d, interpretation |
| `control_chart_rules(vals)` | Western Electric 规则 | center_line, ucl, lcl, rules_triggered |

### SQL 能做的完整计算（不需要 Python）

```
基础统计：AVG/STDDEV/PERCENTILE_CONT/MIN/MAX/COUNT
趋势分析：REGR_SLOPE + REGR_R2（线性回归）
相关性：CORR（Pearson）
离群值：PERCENTILE_CONT(0.25/0.75) → IQR
过程能力：calc_cpk()（PG 扩展）
组间对比：t_test_welch() → t 值 + p 值 + Cohen's d
控制图：control_chart_rules() → Western Electric 4 规则
同环比：LAG/LEAD 窗口函数
排名/分位：RANK/ROW_NUMBER/NTILE/CUME_DIST
累计：SUM OVER (ORDER BY)
分组聚合：GROUP BY + 任意聚合函数
多表关联：JOIN（支持跨 schema 追溯）
条件筛选：WHERE + 子查询 + CTE
```

## 界面布局

```
┌──────────────────────────────────────────────────┬────────────┐
│  疫苗企业数据分析终端                              │ 数据库表    │
├──────────────────────────────────────────────────┤            │
│                                                  │ schema_1   │
│  # 疫苗企业数据分析终端                            │   table_a  │
│  - 模型: deepseek-v4-flash @ 阿里百炼              │   table_b  │
│  - 数据库: myappdb@localhost                      │ schema_2   │
│  - 4 个专家 (📊分析报告 · ⚖️质量监督 · 🔍检索 · 🤖通用) │   table_c  │
│                                                  │            │
│  ▸ 用户问题                                       │            │
│                                                  │            │
│  ⏳ 📊 统计分析 Agent 思考中...                     │            │
│  ⚙ run_query (...)                               │            │
│    ↳ 42 行结果                                    │            │
│                                                  │            │
│  > 📊 **统计分析 Agent**                           │            │
│  [Markdown 分析结论]                               │            │
│                                                  │            │
├──────────────────────────────────────────────────┤            │
│  执行 · 📊 统计分析 | deepseek-v4-flash@aliyun | db | Context ██░ 25% | 轮次 3 │
├──────────────────────────────────────────────────┴────────────┤
│  Ctrl+P 模型  Ctrl+D 选库  Ctrl+T 规划/执行  Ctrl+S 表/SKILL  Ctrl+Y 复制  Ctrl+O 日志  Ctrl+E/W 展开/折叠 │
└──────────────────────────────────────────────────────────────┘
```

- **左侧主区域**：对话区 (Markdown 原生渲染) + 输入栏 + 状态行 + Footer 快捷键栏
- **右侧面板**：Ctrl+S 切换 数据库表 / SKILL 列表
- **状态行**：当前模式 + 当前 Agent 类型 + 模型@后端 + 数据库 + Context 进度条 + 轮次计数
- **多数据库**：支持配置多个 PostgreSQL 数据库，Ctrl+D 弹出选择器切换
- **思考提示**：显示当前激活的 Agent 类型（如 `⏳ 📊 统计分析 Agent 思考中...`）
- **回答标注**：每条回复前标注负责的 Agent 类型，多 Agent 接力时显示完整链路
- **配色**：Dracula 主题 (#282A36 背景, #BD93F9 高亮)
- **Ctrl+P**：弹出模型选择器，Esc 取消
- **Ctrl+D**：弹出数据库选择器（多数据库模式），Esc 取消
- **Ctrl+T**：切换 规划模式 / 执行模式
- **Ctrl+S**：切换侧边栏内容（数据库表 ↔ SKILL 列表）
- **Ctrl+Y**：复制最后一次回答到剪贴板
- **Ctrl+O**：展开/折叠工具调用日志
- **Ctrl+E / Ctrl+W**：展开/折叠侧边栏全部节点

## 规划/执行双模式

| 模式 | 标识 | 行为 |
|------|------|------|
| 执行模式 | 执行 | LLM 可调用数据库工具，执行查询和分析 |
| 规划模式 | 规划 | LLM 纯文本对话，帮助分析需求、设计方案，不调用工具 |

`Ctrl+T` 切换，当前模式显示在输入栏上方状态行。

## 自动规划与质检 (PRE/POST Hooks)

执行模式下，Agent 在执行前会自动生成结构化计划，执行后进行自检，确保回答完整：

```
用户问题
  ↓
PRE-HOOK: 自动规划 — 生成结构化执行计划（不调工具）
  ├─ 一、任务理解
  ├─ 二、数据需求（表名/条件/目的）
  ├─ 三、执行步骤（步骤/工具/参数/产出）
  ├─ 四、输出章节（从"一"开始编号）
  └─ 五、风险点
  ↓
⏸️ 计划展示 → [Enter] 确认  [Esc] 取消    (可关闭: plan_confirm: false)
  ↓
执行: ReAct 循环 — 按计划调工具、查数据、分析
  ↓
POST-HOOK: 自动质检 — 对照清单检查完整性（不调工具）
  ├─ 结构检查: 章节是否连续？是否遗漏？
  ├─ 内容检查: 是否覆盖所有维度？
  └─ 数据检查: 引用是否准确？
  ↓
最终答案
```

**配置**（`~/.vaxport/config.yaml`）：

```yaml
agent:
  auto_plan: true       # PRE-HOOK: 自动生成执行计划
  plan_confirm: true    # 计划生成后暂停等待用户确认
  auto_qc: true         # POST-HOOK: 自动质检答案
```

- `plan_confirm: false` 时计划自动执行，不等待确认（适合熟练用户或强模型）
- 全部关闭即回到无 hooks 模式，与旧版行为一致
- 额外 token 成本：~1300 tokens/查询（约占用 128K 上下文的 1%）

## 按 Agent Temperature 配置

每个 Agent 可独立设置 temperature，TUI 中 Ctrl+P 或 GUI 设置页均可调整：

```yaml
agent:
  agent_temperatures:
    task_assigner: 0.0       # 纯路由分类，需要确定性输出
    general: 0.1             # 工具调用需要精确参数
    analyze_reporter: 0.3    # 分析文本需要一定创造性
    quality_supervision: 0.1 # 质检需要精确判断
    document_search: 0.2     # 检索需要灵活性
```

## 命令参考

| 命令 | 功能 |
|------|------|
| `Ctrl+P` | 弹出模型选择器，切换 LLM 后端/模型 (Esc 取消) |
| `Ctrl+D` | 弹出数据库选择器，切换 PostgreSQL 数据库 (Esc 取消) |
| `Ctrl+T` | 切换 规划模式 / 执行模式 |
| `Ctrl+S` | 切换侧边栏（数据库表 / SKILL 列表） |
| `Ctrl+Y` | 复制最后一次回答到剪贴板 |
| `Ctrl+O` | 展开/折叠工具调用日志 |
| `Ctrl+E` | 展开侧边栏全部节点 |
| `Ctrl+W` | 折叠侧边栏全部节点 |
| `help` | 显示所有命令帮助 |
| `clear` | 清空当前对话上下文 |
| `status` | 显示模型、token 用量、PG 连接状态 |
| `tables` | 数据库表概览 |
| `skills` | 列出已加载的 SKILL |
| `tools` | 列出可用数据库查询工具 |
| `model` | 显示当前模型 |
| `history` | 显示当前会话对话摘要 |
| `debug` | 切换调试模式（显示 Tool 调用链、SQL、耗时） |
| `copy` | 复制最后回答 (同 Ctrl+Y) |
| `/save [name]` | 保存当前会话 |
| `/export [name]` | 导出最后一次回答为 Markdown 文件 |
| `/refresh-schema` | 重新扫描数据库 schema |
| `/model [aliyun\|local]` | 切换 LLM 后端 |
| `exit` / `quit` | 退出 |

## 架构

```
终端交互: textual (TUI)
    ↓
Orchestrator: 6 Agent 编排 (📊统计 📝报告 ⚖️合规 🔍检索 🔔预警 🤖通用)
    ↓
Agent 引擎: ReAct 循环 (Think → Act → Observe) + Handoff 接力
    + PRE-HOOK 自动规划 + POST-HOOK 自动质检
    ↓
LLM 后端: OpenAI 兼容统一接口 → DashScope (deepseek-v4-flash) / Ollama (本地)
    + 自动熔断切换 (云端故障 → 本地)
    + Ctrl+P 动态模型选择 (通过 /v1/models API)
    ↓
工具执行: Schema 自动发现 + 17 内置工具 + 多数据库支持 (Ctrl+D 切换)
    ↓
数据层: PostgreSQL 多库 + pgvector (RAG 文档检索) + 百炼 text-embedding-v4 / qwen-vl-max
    ↓
SKILL 兼容: ~/.agents/skills/ (三级模型)
    ↓
会话管理: JSON 持久化 + 审计日志 (GMP 合规)
```

## 工作流程

### 简单任务（GeneralAgent，无 pipeline）

```
用户: "PEDV-2024 的效价趋势怎么样"
         │
         ▼
GeneralAgent (auto_plan=False)
  ├─ 理解需求: 年度效价趋势
  ├─ 写 SQL: SELECT batch_no, potency FROM ... WHERE batch_id LIKE 'PEDV-2024%'
  ├─ 如有需要: generate_chart
  └─ 翻译输出: "效价整体稳定, 均值7.2, 无显著下降趋势"

延迟: ~3-5 秒
```

### 复杂任务（专业 Agent，完整 pipeline）

```
用户: "评估质量体系成熟度，生成 APQR"
         │
         ▼
GeneralAgent → [HANDOFF:analyze_reporter]
         │
         ▼
AnalyzeReporter (auto_plan=True)
  ├─ ① plan: 生成结构化执行计划（任务理解/数据需求/步骤/输出章节/风险点）
  ├─ ② confirm: 展示计划 → 等待用户确认（Enter 执行 / Esc 取消）
  ├─ ③ SQL batch: 批量执行 5-15 条查询采集数据
  ├─ ④ ReAct: 多轮分析（调 detect_anomaly/calc_cpk/generate_chart/generate_report）
  ├─ ⑤ QC: 自动质检（章节完整性/数据一致性/法规引用）
  └─ ⑥ fix: 如有问题自动修复（最多 3 轮审核-修复循环）

延迟: ~30-90 秒
```

### 多 Agent 接力

```
用户: "分析效价趋势，如有异常调查根因，生成偏差报告"
         │
         ▼
GeneralAgent → [HANDOFF:analyze_reporter]
  ├─ 趋势分析 → 异常检测 → 发现异常
  └─ [HANDOFF:quality_supervision]
       ├─ 偏差分级 → 根因分析
       └─ [HANDOFF:analyze_reporter]
            └─ generate_report(deviation_report) → 最终输出
```

### pipeline vs 无 pipeline

| | GeneralAgent | AnalyzeReporter / QualitySupervision |
|------|------|------|
| 触发 | 所有查询入口 | Handoff 激活 |
| plan | 无 | 自动生成执行计划 |
| confirm | 无 | 等待用户确认 |
| SQL batch | 无 | 批量采集 |
| QC + fix | 无 | 自动质检 + 修复 |
| 延迟 | ~5 秒 | ~30-90 秒 |
| 适用 | 查表/简单统计/追问 | 深度分析/异常检测/报告 |

## 设计原则与 Agent 分工

### 核心原则：SQL 是计算引擎，LLM 是交互界面

```
┌─────────────────────────────────────────────────────────┐
│                     PostgreSQL                          │
│  计算引擎：AVG · STDDEV · CORR · LAG · PERCENTILE_CONT  │
│           REGR_SLOPE · REGR_R2 · 窗口函数 · 聚合       │
│           → 一次 SQL 完成，毫秒级                        │
├─────────────────────────────────────────────────────────┤
│                     LLM (DeepSeek)                      │
│  交互界面：理解中文需求 → 写 SQL → 翻译结果              │
│           → 不做 SQL 能做的计算                          │
└─────────────────────────────────────────────────────────┘
```

**第一性原则**：PostgreSQL 是图灵完备的计算引擎，内置 200+ 统计/分析/窗口函数。数据提取到 Python 再让 LLM 算均值、做回归是反模式——慢、贵、不准。

| 场景 | 错误做法 | 正确做法 |
|------|---------|---------|
| 求批次效价均值 | 查数据 → LLM 手算 `(a+b+c)/3` | `SELECT AVG(potency)` |
| 趋势检测 | 查数据 → LLM 画线估计斜率 | `SELECT REGR_SLOPE(value, batch_no)` |
| 相关性分析 | 查两列 → LLM 肉眼看关联 | `SELECT CORR(temp, potency)` |
| 同比/环比 | 查两年数据 → LLM 分别算比例 | `SELECT ... LAG() OVER()` |
| 百分位 | 查全部数据 → LLM 排序数数 | `SELECT PERCENTILE_CONT(0.95)` |
| 分组Top-N | 查全部数据 → LLM 手选 | `SELECT ... ROW_NUMBER() OVER(PARTITION BY)` |

**推论**：LLM 的任务是理解需求 → 写出正确的 SQL → 把结果翻译成人话。不要让 LLM 做 SQL 能做的事。

### Agent 能力矩阵

每个 Agent 的工具集是**代码级硬约束**（`TOOL_FILTERS`），不是 prompt 建议。Agent 物理上没有它不该调的工具。

```
                         ┌─────────────────────────────┐
  用户输入               │      GeneralAgent            │
        │                │  工具: query_*               │
        ▼                │       generate_chart         │
  ┌──────────┐           │  auto_plan: False            │
  │ General  │           │                             │
  │ Agent    │           │  简单 → SQL计算+翻译 → 回答  │
  └────┬─────┘           │  复杂 → HANDOFF → 专业Agent  │
       │                 └─────────────────────────────┘
       │ [HANDOFF:stats|report|compliance|doc_search|alert_monitor]
       ▼
  ┌──────────────────────────────────────────────────────┐
  │  专业 Agent (完整 pipeline)                           │
  │  plan → confirm → SQL batch → ReAct → QC → fix       │
  ├──────────┬──────────┬──────────┬──────────┬─────────┤
  │ 📊 统计  │ 📝 报告  │ ⚖️ 合规  │ 🔍 检索  │ 🔔 预警 │
  │ query_*  │ query_*  │ query_*  │ search_  │ query_*  │
  │ run_stat │ generate │ match_r  │ index_   │ check_a  │
  │ detect_a │ _report  │ root_c   │ doc      │ get_aler │
  │ gen_char │ gen_char │ classif  │ gen_char │ detect_a │
  │          │          │ check_c  │          │ gen_char │
  │          │          │ check_o  │          │          │
  │          │          │ gen_char │          │          │
  └──────────┴──────────┴──────────┴──────────┴─────────┘
```

| Agent | 工具集 | 适用场景 |
|-------|--------|---------|
| **GeneralAgent** | `query_*`, `generate_chart` | 统一入口，简单查表、基础统计、数据浏览、对话追问、图表生成 |
| **AnalyzeReporter** | +`detect_anomaly`, `generate_report` | Cpk 过程能力、趋势分析、异常检测、显著性检验、控制图、GMP 合规报告（APQR/批记录/偏差/月度质量） |
| **QualitySupervisionAgent** | `query_*`, `generate_chart` | 偏差分类、CAPA 跟踪、OOS 调查、法规匹配、预警监控、审计追踪（价值在 System Prompt 专业知识） |
| **DocSearchAgent** | `search_documents`, `index_documents`, `generate_chart` | SOP 检索、法规检索、文献检索、历史批次检索 |

### GeneralAgent — 统一入口设计

**为什么需要 GeneralAgent？**

门控（Gating）方案经历了三次迭代均失败：
1. **合并门控**：门控指令嵌入 PLAN_PROMPT → LLM 被 90 行模板 prime 成规划模式，无法正确判断
2. **独立门控**：独立轻量 LLM 调用做 CHAT/ANALYZE 二分 → "简单查库"和"复杂分析"都需要数据库，二元分类维度不匹配
3. **根因**：简单查数据库（如"批次 PEDV-2024-0187 的效价是多少"）和复杂分析（如"用 ISO 9001 框架评估质量体系成熟度"）在 CHAT/ANALYZE 维度上无法区分

**GeneralAgent 方案**：不分类，统一入口。通过**工具约束**控制行为：

- GeneralAgent 只有 `query_*` + `generate_chart`，`auto_plan=False`
  - 简单查表 → SQL 查询 → 翻译 → 直接回答（无 pipeline 开销）
  - 对话追问 → 基于历史上下文直接回答
  - 简单统计 → SQL 内置函数（AVG/CORR/REGR_*/PERCENTILE）计算 → 翻译
- 遇到需要专业工具的任务（`detect_anomaly`/`generate_report` 等）
  → 输出 `[HANDOFF:analyze_reporter]` → Orchestrator 路由到专业 Agent
  → 专业 Agent 执行完整 pipeline（plan→confirm→SQL batch→QC→fix）

**关键设计决策**：
- GeneralAgent `auto_plan=False`：简单任务不生成规划、不等待确认、不走 QC，低延迟
- 工具约束是硬约束：GeneralAgent 物理上没有 `detect_anomaly`，不可能越权
- Handoff 保留：复杂任务无缝切换到专业 Agent 的完整 pipeline

## 技术栈

- Python 3.12+
- LLM: openai SDK (OpenAI 兼容协议) + dashscope SDK (多模态)
- PG: psycopg2 + pgvector (RAG 向量检索)
- Embedding: 百炼 text-embedding-v4 (1536d)
- 图表: matplotlib (Agg backend)
- 报告: jinja2 模板引擎
- TUI: textual
- API: **FastAPI** (新)
- 配置: pyyaml
- Token: tiktoken

## 安全

- 只读 PG 账号（无 INSERT/UPDATE/DELETE 权限）
- 参数化查询（%s 占位符，防 SQL 注入）
- 查询超时 30s
- 结果限制 5000 行（LIMIT+1 真截断检测，截断时返回警告引导 LLM 加过滤条件）
- 审计日志（追加式 JSON 行）

## 关键数字

- **4 Agent**：通用入口 / 分析报告 / 质量监督 / 文档检索
- **3 PG 扩展**：calc_cpk / t_test_welch / control_chart_rules
- **5 报告类型**：APQR / 批记录 / 偏差报告 / 批签发 / 月度报告
- **3 异常检测**：OOT / 参数漂移 / 设备劣化
- **max_rounds**: 100 · **total_timeout**: 不限（v1.1.0 起默认关闭，由 CPU 卡死检测守护） · **结果限制**: 5000 行
- **上下文压缩**：超 75% 窗口触发 · 保留最近 3 轮对话

## 版本历史

### v1.3.0 (2026-06-03)

- **跨平台打包**: 支持 DMG (macOS) / EXE (Windows) / DEB (Linux) 一键安装，PyInstaller 全量打包 + Tauri 套壳

### v1.2.0 (2026-06-03)

- **按 Agent 设置 Temperature**：取消全局 temperature，改为每个 Agent 独立配置（任务分配 0.0 / 通用 0.1 / 分析报告 0.3 / 质量监督 0.1 / 文档检索 0.2）
- **LLM 随机性缓解**：语义相似度检测 + 批量处理识别（多样性阈值 0.6），防止 Agent 重复调用相同工具，同时不误判合法批量操作
- **上下文注入**：LLM 调用前注入历史调用摘要，引导 LLM 避免无意义重复
- **图表链路打通**：Agent → SSE → 前端完整图表数据流（base64 PNG）
- **TUI 模型列表精简**：Ctrl+P 仅展示 4 个指定模型（deepseek-v4-pro/flash、qwen3.7-max、qwen-max）
- **规划输出清理**：PLAN_PROMPT 禁止输出"确认后执行"等对话性文本
- **会话清理**：过滤 0 条消息的无效会话记录
- **`_append_tool_results` None 守卫**：修复 state 参数为 None 时的 AttributeError

### v1.1.0 (2026-06-02)

- **取消内部超时截断**：`total_timeout` 默认值 900s → 0（不限制），不再出现"分析超时"截断输出
- **httpx 超时保护**：LLM API 调用加 `httpx.Timeout(600s, connect=30s)`，防止 API 无限挂起
- **CPU 卡死检测**：Agent ReAct 循环内建 daemon 监控线程，连续 5 次 CPU=0%（60s 间隔）且运行 >20 分钟 → 自动退出
- **批量测试框架**：`scripts/test_runner.py` 支持 pause/continue、进度持久化、自动卡死检测
- **回归测试**：60 题全量通过（vs v1.0.0 仅 21%），字体问题清零

### v1.0.0 (2026-05-31)

- 初始版本：4 Agent 架构、ReAct 循环、上下文压缩、自动审核修复