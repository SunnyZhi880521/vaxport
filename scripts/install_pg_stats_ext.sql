-- ============================================================================
-- vaxport PostgreSQL 统计扩展函数
-- 用途: 在 PG 服务器上以超级用户执行，创建统计计算函数供 Agent 使用
-- 执行: psql -h <host> -U postgres -d <dbname> -f install_pg_stats_ext.sql
-- ============================================================================

-- 1. 启用 plpython3u（如未启用）
CREATE EXTENSION IF NOT EXISTS plpython3u;

-- ============================================================================
-- 函数 1: calc_cpk — 过程能力指数
-- 用法: SELECT * FROM calc_cpk(ARRAY[7.1,7.3,7.0,...], 8.0, 6.0);
-- ============================================================================

CREATE OR REPLACE FUNCTION calc_cpk(
    vals double precision[],
    usl double precision DEFAULT NULL,
    lsl double precision DEFAULT NULL
)
RETURNS TABLE(
    n bigint,
    mean_val double precision,
    std_val double precision,
    cp double precision,
    cpk double precision,
    cpu double precision,
    cpl double precision,
    judgment text
)
LANGUAGE plpython3u
AS $$
import math
if not vals or len(vals) < 2:
    return []
n = len(vals)
m = sum(vals) / n
s = math.sqrt(sum((v - m)**2 for v in vals) / (n - 1))

cp = None
cpk = None
cpu = None
cpl = None
judgment = '数据不足'

if s == 0:
    return [(n, round(m, 4), 0, None, None, None, None, '标准差为0，所有值相同')]

if usl is not None and lsl is not None:
    cp = (usl - lsl) / (6 * s)
    cpu = (usl - m) / (3 * s)
    cpl = (m - lsl) / (3 * s)
    cpk = min(cpu, cpl)
elif usl is not None:
    cpu = (usl - m) / (3 * s)
    cpk = cpu
elif lsl is not None:
    cpl = (m - lsl) / (3 * s)
    cpk = cpl

if cpk is not None:
    if cpk >= 1.33:
        judgment = '过程能力充分'
    elif cpk >= 1.0:
        judgment = '基本合格，建议持续监控'
    else:
        judgment = '过程能力不足，需启动CAPA'

return [(n, round(m, 4), round(s, 4),
         round(cp, 4) if cp else None,
         round(cpk, 4) if cpk else None,
         round(cpu, 4) if cpu else None,
         round(cpl, 4) if cpl else None,
         judgment)]
$$;

-- ============================================================================
-- 函数 2: t_test_welch — Welch's t 检验
-- 用法: SELECT * FROM t_test_welch(ARRAY[7.1,7.3,...], ARRAY[6.5,6.8,...]);
-- ============================================================================

CREATE OR REPLACE FUNCTION t_test_welch(
    a double precision[],
    b double precision[]
)
RETURNS TABLE(
    n_a bigint,
    n_b bigint,
    mean_a double precision,
    mean_b double precision,
    std_a double precision,
    std_b double precision,
    t_stat double precision,
    df double precision,
    p_value double precision,
    cohens_d double precision,
    effect_size text,
    significant boolean,
    interpretation text
)
LANGUAGE plpython3u
AS $$
import math

def _beta_cf(a_val, b_val, x):
    """Lentz连分式 — 不完全Beta函数"""
    log_beta = math.lgamma(a_val) + math.lgamma(b_val) - math.lgamma(a_val + b_val)
    front = math.exp(a_val * math.log(x) + b_val * math.log(1 - x) - log_beta) / a_val
    f = 1.0
    c = 1.0
    d = 1.0 - (a_val + b_val) * x / (a_val + 1)
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    f = d
    for m in range(1, 200):
        numer = m * (b_val - m) * x / ((a_val + 2 * m - 1) * (a_val + 2 * m))
        d = 1.0 + numer * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + numer / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        f *= d * c
        numer = -(a_val + m) * (a_val + b_val + m) * x / ((a_val + 2 * m) * (a_val + 2 * m + 1))
        d = 1.0 + numer * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + numer / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        f *= delta
        if abs(delta - 1.0) < 1e-10:
            break
    return front * f

def t_pvalue(t, df):
    if df <= 0:
        return 1.0
    x = df / (df + t * t)
    a_val = df / 2.0
    b_val = 0.5
    if x < (a_val + 1) / (a_val + b_val + 2):
        p = _beta_cf(a_val, b_val, x)
    else:
        p = 1.0 - _beta_cf(b_val, a_val, 1 - x)
    return max(0.0, min(1.0, p))

if not a or not b or len(a) < 2 or len(b) < 2:
    return []

na = len(a)
nb = len(b)
ma = sum(a) / na
mb = sum(b) / nb
sa = math.sqrt(sum((v - ma)**2 for v in a) / (na - 1)) if na > 1 else 0
sb = math.sqrt(sum((v - mb)**2 for v in b) / (nb - 1)) if nb > 1 else 0

diff = ma - mb
se = math.sqrt(sa**2 / na + sb**2 / nb)
if se == 0:
    return []

t_stat = diff / se
num = (sa**2 / na + sb**2 / nb)**2
den = (sa**2 / na)**2 / (na - 1) + (sb**2 / nb)**2 / (nb - 1)
df = num / den
p_value = t_pvalue(abs(t_stat), df)

pooled_sd = math.sqrt(((na - 1) * sa**2 + (nb - 1) * sb**2) / (na + nb - 2))
cohens_d = abs(diff) / pooled_sd if pooled_sd != 0 else 0

if cohens_d >= 0.8:
    es = '大效应'
elif cohens_d >= 0.5:
    es = '中效应'
else:
    es = '小效应'

sig = p_value < 0.05
interp = (
    f"{'差异显著' if sig else '差异不显著'} "
    f"(t={round(t_stat, 2)}, p={round(p_value, 4)}, d={round(cohens_d, 2)} [{es}]), "
    f"A组均值={round(ma, 2)}, B组均值={round(mb, 2)}"
)

return [(na, nb, round(ma, 4), round(mb, 4), round(sa, 4), round(sb, 4),
         round(t_stat, 4), round(df, 2), round(p_value, 4),
         round(cohens_d, 4), es, sig, interp)]
$$;

-- ============================================================================
-- 函数 3: control_chart_rules — Western Electric 控制图规则检测
-- 用法: SELECT * FROM control_chart_rules(ARRAY[7.1,7.3,...]);
-- ============================================================================

CREATE OR REPLACE FUNCTION control_chart_rules(
    vals double precision[],
    sigma double precision DEFAULT 3
)
RETURNS TABLE(
    n bigint,
    mean_val double precision,
    std_val double precision,
    center_line double precision,
    ucl double precision,
    lcl double precision,
    in_control boolean,
    rules_triggered text
)
LANGUAGE plpython3u
AS $$
import math
if not vals or len(vals) < 2:
    return []

n = len(vals)
m = sum(vals) / n
s = math.sqrt(sum((v - m)**2 for v in vals) / (n - 1))
ucl = m + sigma * s
lcl = m - sigma * s

beyond_ucl = [i+1 for i, v in enumerate(vals) if v > ucl]
beyond_lcl = [i+1 for i, v in enumerate(vals) if v < lcl]

rules = []

# Rule 1
if beyond_ucl or beyond_lcl:
    pts = beyond_ucl + beyond_lcl
    rules.append(f"Rule1: {len(pts)}点超出{sigma}σ控制限 (位置:{pts})")

# Rule 2
above = sum(1 for v in vals if v > m)
below = sum(1 for v in vals if v < m)
if above >= 7:
    rules.append(f"Rule2: 连续{above}点在中心线上方")
if below >= 7:
    rules.append(f"Rule2: 连续{below}点在中心线下方")

# Rule 3
if n >= 7:
    for start in range(n - 6):
        window = vals[start:start+7]
        if all(window[i] < window[i+1] for i in range(6)):
            rules.append(f"Rule3: 连续7点上升(idx {start+1}-{start+7})")
            break
        if all(window[i] > window[i+1] for i in range(6)):
            rules.append(f"Rule3: 连续7点下降(idx {start+1}-{start+7})")
            break

# Rule 4
if n >= 14:
    for start in range(n - 13):
        window = vals[start:start+14]
        alt = sum(1 for i in range(1, 13)
                  if (window[i]-window[i-1])*(window[i+1]-window[i]) < 0)
        if alt >= 12:
            rules.append(f"Rule4: 连续14点交替(idx {start+1}-{start+14})")
            break

in_ctrl = len(rules) == 0
return [(n, round(m, 4), round(s, 4), round(m, 4),
         round(ucl, 4), round(lcl, 4), in_ctrl,
         '; '.join(rules) if rules else '无异常')]
$$;

-- ============================================================================
-- 授权只读用户
-- ============================================================================
GRANT EXECUTE ON FUNCTION calc_cpk(double precision[], double precision, double precision) TO vlm_reader;
GRANT EXECUTE ON FUNCTION t_test_welch(double precision[], double precision[]) TO vlm_reader;
GRANT EXECUTE ON FUNCTION control_chart_rules(double precision[], double precision) TO vlm_reader;

-- ============================================================================
-- 验证（以 vlm_reader 身份测试）
-- ============================================================================
-- SELECT * FROM calc_cpk(ARRAY[7.1,7.3,7.0,7.4,7.2,6.8,7.5,7.1], 8.0, 6.0);
-- SELECT * FROM t_test_welch(ARRAY[7.1,7.3,7.0,7.4,7.2], ARRAY[6.5,6.8,6.3,6.9,6.6]);
-- SELECT * FROM control_chart_rules(ARRAY[7.1,7.3,7.0,7.4,7.2,6.8,7.5,7.1,7.0,7.3,7.2,7.1,6.9,7.4,7.0]);