<?php
/**
 * 统一配置管理页面
 * 用户手动输入各平台 Cookie/Token，咪咕账号密码
 * 支持咪咕每3小时自动刷新凭证
 * 安全: 需要URL携带 ?key=xxx 才能访问
 */
$configFile = __DIR__ . '/config.json';
$logDir = __DIR__ . '/logs';
if (!is_dir($logDir)) mkdir($logDir, 0775, true);

// ==================== 密钥验证 ====================
$keyFile = __DIR__ . '/data/admin_key.txt';
$storedKey = file_exists($keyFile) ? trim(file_get_contents($keyFile)) : '';
$inputKey = $_GET['key'] ?? '';

if (!$storedKey) {
    http_response_code(403);
    echo '<!DOCTYPE html><html><head><meta charset="UTF-8"><title>403</title></head><body style="font-family:sans-serif;text-align:center;padding-top:100px;">';
    echo '<h2>🔒 管理后台未初始化</h2>';
    echo '<p>请先在服务器命令行运行：<code>php gen_key.php</code></p>';
    echo '<p>生成密钥后，用带密钥的链接访问本页面</p>';
    echo '</body></html>';
    exit;
}

if (!hash_equals($storedKey, $inputKey)) {
    http_response_code(403);
    echo '<!DOCTYPE html><html><head><meta charset="UTF-8"><title>403</title></head><body style="font-family:sans-serif;text-align:center;padding-top:100px;">';
    echo '<h2>🚫 访问被拒绝</h2>';
    echo '<p>密钥错误或缺失，请使用正确的带密钥链接访问</p>';
    echo '<p>如忘记密钥，重新运行 <code>php gen_key.php</code> 生成新密钥</p>';
    echo '</body></html>';
    exit;
}
// ==================================================

// 加载配置
$config = json_decode(file_get_contents($configFile), true);
$msg = '';
$msgType = '';

// 处理表单提交
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = $_POST['action'] ?? '';

    if ($action === 'save') {
        // 酷狗
        if (!empty($_POST['kg_userid']) && !empty($_POST['kg_token'])) {
            $config['modules']['platform']['kg']['users'] = [[
                'userid' => trim($_POST['kg_userid']),
                'token' => trim($_POST['kg_token']),
                'cookie' => trim($_POST['kg_cookie'] ?? ''),
                'refreshLogin' => true,
            ]];
        }
        // QQ
        if (!empty($_POST['tx_uin']) && !empty($_POST['tx_token'])) {
            $config['modules']['platform']['tx']['users'] = [[
                'uin' => trim($_POST['tx_uin']),
                'token' => trim($_POST['tx_token']),
                'refreshKey' => trim($_POST['tx_refreshkey'] ?? ''),
                'cookie' => trim($_POST['tx_cookie'] ?? ''),
                'vipType' => trim($_POST['tx_viptype'] ?? 'svip'),
                'refreshLogin' => true,
            ]];
        }
        // 网易云
        if (!empty($_POST['wy_cookie'])) {
            $config['wy']['cookie'] = trim($_POST['wy_cookie']);
        }
        // 咪咕
        if (!empty($_POST['migu_phone'])) {
            $config['migu']['accounts'] = [trim($_POST['migu_phone'])];
            $config['migu']['password'] = trim($_POST['migu_password'] ?? '');
            $config['migu']['sourceID'] = trim($_POST['migu_sourceid'] ?? '220029');
        }
        $config['migu']['auto_refresh_hours'] = intval($_POST['migu_refresh_hours'] ?? 3);

        file_put_contents($configFile, json_encode($config, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
        $msg = '配置已保存';
        $msgType = 'success';
    }

    if ($action === 'save_security') {
        $config['security']['api_key'] = trim($_POST['api_key'] ?? '');
        $config['security']['api_key_enabled'] = isset($_POST['api_key_enabled']) ? true : false;
        $config['security']['cors_enabled'] = isset($_POST['cors_enabled']) ? true : false;
        $corsOrigins = trim($_POST['cors_origins'] ?? '*');
        $config['security']['cors_origins'] = array_filter(array_map('trim', explode("\n", $corsOrigins)));
        $config['security']['hide_errors'] = isset($_POST['hide_errors']) ? true : false;
        file_put_contents($configFile, json_encode($config, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
        $msg = '安全设置已保存';
        $msgType = 'success';
    }

    if ($action === 'refresh_all') {
        $cmd = 'php ' . escapeshellarg(__DIR__ . '/refresh_cron.php') . ' 2>&1';
        exec($cmd, $output, $retCode);
        $result = implode("\n", $output);
        if ($retCode === 0) {
            $msg = "全平台凭证刷新完成\n" . $result;
            $msgType = 'success';
        } else {
            $msg = "部分平台刷新失败:\n" . $result;
            $msgType = 'error';
        }
        // 重新加载config
        $config = json_decode(file_get_contents($configFile), true);
    }

    if ($action === 'refresh_migu') {
        // 立即刷新咪咕凭证
        $phone = $config['migu']['accounts'][0] ?? '';
        $password = $config['migu']['password'] ?? '';
        if ($phone && $password) {
            $output = [];
            $cmd = sprintf('php %s/migu_login.php "%s" "%s" 2>&1',
                escapeshellarg(__DIR__),
                escapeshellarg($phone),
                escapeshellarg($password)
            );
            exec($cmd, $output, $retCode);
            $result = implode("\n", $output);
            if ($retCode === 0 && strpos($result, '流程完成') !== false) {
                $config['migu']['last_refresh'] = date('Y-m-d H:i:s');
                file_put_contents($configFile, json_encode($config, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
                $msg = '咪咕凭证刷新成功';
                $msgType = 'success';
            } else {
                $msg = '咪咕刷新失败: ' . substr($result, -200);
                $msgType = 'error';
            }
        } else {
            $msg = '请先填写咪咕账号密码';
            $msgType = 'error';
        }
    }
}

// 读取当前配置
$kg = $config['modules']['platform']['kg']['users'][0] ?? [];
$tx = $config['modules']['platform']['tx']['users'][0] ?? [];
$wy = $config['wy'] ?? [];
$migu = $config['migu'] ?? [];
$refreshHours = $migu['auto_refresh_hours'] ?? 3;
$lastRefresh = $migu['last_refresh'] ?? '从未';
?>
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>音乐API - 配置管理</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f7; color: #333; padding: 20px; }
.container { max-width: 800px; margin: 0 auto; }
h1 { font-size: 24px; margin-bottom: 20px; color: #1a1a1a; }
.card { background: #fff; border-radius: 12px; padding: 24px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.card h2 { font-size: 18px; margin-bottom: 16px; color: #1a1a1a; border-bottom: 1px solid #eee; padding-bottom: 10px; }
.form-group { margin-bottom: 14px; }
.form-group label { display: block; font-size: 13px; color: #666; margin-bottom: 4px; font-weight: 500; }
.form-group input, .form-group textarea { width: 100%; padding: 10px 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; font-family: monospace; }
.form-group textarea { min-height: 60px; resize: vertical; }
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.btn { padding: 12px 24px; border: none; border-radius: 8px; font-size: 15px; cursor: pointer; font-weight: 500; transition: all 0.2s; }
.btn-primary { background: #007aff; color: #fff; }
.btn-primary:hover { background: #0056cc; }
.btn-success { background: #34c759; color: #fff; }
.btn-success:hover { background: #28a745; }
.btn-group { display: flex; gap: 12px; margin-top: 20px; }
.msg { padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; font-size: 14px; }
.msg.success { background: #d4edda; color: #155724; }
.msg.error { background: #f8d7da; color: #721c24; }
.status { font-size: 12px; color: #999; margin-top: 4px; }
.hint { font-size: 12px; color: #999; margin-top: 2px; }
.platform-tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; margin-left: 8px; }
.tag-kg { background: #222; color: #fff; }
.tag-tx { background: #31c27c; color: #fff; }
.tag-wy { background: #c20c0c; color: #fff; }
.tag-kw { background: #3281f4; color: #fff; }
.tag-migu { background: #ff6b00; color: #fff; }
.cron-box { background: #f8f8f8; padding: 12px; border-radius: 8px; font-family: monospace; font-size: 12px; margin-top: 10px; word-break: break-all; }
</style>
</head>
<body>
<div class="container">
    <h1>🎵 音乐API 配置管理</h1>

    <?php if ($msg): ?>
    <div class="msg <?php echo $msgType; ?>"><?php echo htmlspecialchars($msg); ?></div>
    <?php endif; ?>

    <form method="POST" action="?key=<?php echo urlencode($inputKey); ?>">
    <input type="hidden" name="action" value="save">

    <!-- 酷狗 -->
    <div class="card">
        <h2>酷狗音乐 <span class="platform-tag tag-kg">KG</span></h2>
        <div class="form-row">
            <div class="form-group">
                <label>UserID</label>
                <input type="text" name="kg_userid" value="<?php echo htmlspecialchars($kg['userid'] ?? ''); ?>" placeholder="如 123456789">
            </div>
            <div class="form-group">
                <label>Token</label>
                <input type="text" name="kg_token" value="<?php echo htmlspecialchars($kg['token'] ?? ''); ?>" placeholder="64位token">
            </div>
        </div>
        <div class="form-group">
            <label>Cookie（可选）</label>
            <textarea name="kg_cookie" placeholder="KugooID=...; t=...; token=..."><?php echo htmlspecialchars($kg['cookie'] ?? ''); ?></textarea>
        </div>
        <div class="hint">从 LX Music 客户端导出，或访问酷狗网页版登录后从浏览器复制</div>
    </div>

    <!-- QQ音乐 -->
    <div class="card">
        <h2>QQ音乐 <span class="platform-tag tag-tx">TX</span></h2>
        <div class="form-group">
            <label>粘贴完整 Cookie 自动解析（推荐）</label>
            <textarea id="tx_cookie_paste" placeholder="从浏览器登录 y.qq.com 后复制完整 Cookie 粘贴到这里，自动提取 uin / token / refreshKey 等字段" oninput="parseTxCookie()"></textarea>
            <div class="hint" id="tx_parse_hint">粘贴后自动填充下方字段，无需手动逐个填写</div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>UIN（QQ号）</label>
                <input type="text" name="tx_uin" id="tx_uin" value="<?php echo htmlspecialchars($tx['uin'] ?? ''); ?>" placeholder="如 123456789">
            </div>
            <div class="form-group">
                <label>VIP类型</label>
                <input type="text" name="tx_viptype" value="<?php echo htmlspecialchars($tx['vipType'] ?? 'svip'); ?>" placeholder="svip / vip">
            </div>
        </div>
        <div class="form-group">
            <label>Token（qm_keyst）</label>
            <input type="text" name="tx_token" id="tx_token" value="<?php echo htmlspecialchars($tx['token'] ?? ''); ?>" placeholder="Q_H_L_... 开头">
        </div>
        <div class="form-group">
            <label>RefreshKey</label>
            <input type="text" name="tx_refreshkey" id="tx_refreshkey" value="<?php echo htmlspecialchars($tx['refreshKey'] ?? ''); ?>" placeholder="用于自动续签">
        </div>
        <div class="form-group">
            <label>Cookie（可选）</label>
            <textarea name="tx_cookie" placeholder="uin=...; qqmusic_key=..."><?php echo htmlspecialchars($tx['cookie'] ?? ''); ?></textarea>
        </div>
        <div class="hint">QQ音乐自带自动续签（refreshLogin），填写 refreshKey 后会自动刷新token</div>
    </div>

    <!-- 网易云 -->
    <div class="card">
        <h2>网易云音乐 <span class="platform-tag tag-wy">WY</span></h2>
        <div class="form-group">
            <label>Cookie</label>
            <textarea name="wy_cookie" placeholder="MUSIC_U=...; __csrf=..."><?php echo htmlspecialchars($wy['cookie'] ?? ''); ?></textarea>
        </div>
        <div class="hint">登录 music.163.com 后从浏览器复制完整Cookie</div>
    </div>

    <!-- 酷我 -->
    <div class="card">
        <h2>酷我音乐 <span class="platform-tag tag-kw">KW</span></h2>
        <div class="hint">酷我无需配置账号，匿名即可获取播放直链</div>
    </div>

    <!-- 咪咕 -->
    <div class="card">
        <h2>咪咕音乐 <span class="platform-tag tag-migu">MIGU</span></h2>
        <div class="form-row">
            <div class="form-group">
                <label>手机号</label>
                <input type="text" name="migu_phone" value="<?php echo htmlspecialchars($migu['accounts'][0] ?? ''); ?>" placeholder="咪咕登录手机号">
            </div>
            <div class="form-group">
                <label>密码</label>
                <input type="password" name="migu_password" value="<?php echo htmlspecialchars($migu['password'] ?? ''); ?>" placeholder="登录密码">
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>SourceID</label>
                <input type="text" name="migu_sourceid" value="<?php echo htmlspecialchars($migu['sourceID'] ?? '220029'); ?>">
            </div>
            <div class="form-group">
                <label>自动刷新间隔（小时）</label>
                <input type="number" name="migu_refresh_hours" value="<?php echo $refreshHours; ?>" min="1" max="24">
            </div>
        </div>
        <div class="status">上次刷新: <?php echo $lastRefresh; ?></div>
        <div class="cron-box">
            <strong>自动刷新Cron命令（每<?php echo $refreshHours; ?>小时）：</strong><br>
            0 */<?php echo $refreshHours; ?> * * * curl -s "<?php echo (isset($_SERVER['HTTPS']) ? 'https' : 'http') . '://' . $_SERVER['HTTP_HOST'] . dirname($_SERVER['PHP_SELF']); ?>/migu_cron.php" > /dev/null 2>&1
        </div>
    </div>

    <div class="btn-group">
        <button type="submit" class="btn btn-primary">💾 保存全部配置</button>
    </div>
    </form>

    <!-- 凭证管理 -->
    <div class="card">
        <h2>凭证自动刷新</h2>
        <form method="POST" action="?key=<?php echo urlencode($inputKey); ?>" style="display:inline;">
            <input type="hidden" name="action" value="refresh_all">
            <button type="submit" class="btn btn-success">🔄 立即刷新全部平台凭证</button>
        </form>
        <form method="POST" action="?key=<?php echo urlencode($inputKey); ?>" style="display:inline; margin-left:10px;">
            <input type="hidden" name="action" value="refresh_migu">
            <button type="submit" class="btn">仅刷新咪咕</button>
        </form>
        <div class="cron-box" style="margin-top:14px;">
            <strong>全平台自动刷新 Cron（推荐，每6小时刷新酷狗/QQ/咪咕）：</strong><br><br>
            0 */6 * * * curl -s "<?php echo (isset($_SERVER['HTTPS']) ? 'https' : 'http') . '://' . $_SERVER['HTTP_HOST'] . dirname($_SERVER['PHP_SELF']); ?>/refresh_cron.php" > /dev/null 2>&1
        </div>
        <div class="hint" style="margin-top:10px;">
            酷狗/QQ通过refreshKey自动续期token；咪咕通过账号密码重新登录；网易云Cookie有效期较长无需频繁刷新。
        </div>
    </div>

    <!-- 安全设置 -->
    <div class="card">
        <h2>🔒 安全设置</h2>
        <form method="POST" action="?key=<?php echo urlencode($inputKey); ?>">
        <input type="hidden" name="action" value="save_security">
        <div class="form-group">
            <label>
                <input type="checkbox" name="api_key_enabled" <?php echo !empty($config['security']['api_key_enabled']) ? 'checked' : ''; ?>>
                启用 API Key 鉴权（所有接口需带 ?key=xxx）
            </label>
        </div>
        <div class="form-group">
            <label>API Key</label>
            <input type="text" name="api_key" value="<?php echo htmlspecialchars($config['security']['api_key'] ?? ''); ?>" placeholder="留空则不鉴权">
        </div>
        <div class="form-group">
            <label>
                <input type="checkbox" name="cors_enabled" <?php echo !empty($config['security']['cors_enabled']) ? 'checked' : ''; ?>>
                启用 CORS 白名单（不启用则允许所有来源）
            </label>
        </div>
        <div class="form-group">
            <label>允许的来源（每行一个，* 表示所有）</label>
            <textarea name="cors_origins" rows="3"><?php echo htmlspecialchars(implode("\n", $config['security']['cors_origins'] ?? ['*'])); ?></textarea>
        </div>
        <div class="form-group">
            <label>
                <input type="checkbox" name="hide_errors" <?php echo !empty($config['security']['hide_errors']) ? 'checked' : ''; ?>>
                隐藏详细错误信息（生产环境建议开启，不返回内部路径/Traceback）
            </label>
        </div>
        <button type="submit" class="btn btn-primary">💾 保存安全设置</button>
        </form>
    </div>

</div>

<script>
function parseTxCookie() {
    const raw = document.getElementById('tx_cookie_paste').value.trim();
    if (!raw) return;
    const pairs = {};
    raw.split(';').forEach(part => {
        const idx = part.indexOf('=');
        if (idx > 0) {
            const k = part.substring(0, idx).trim();
            const v = part.substring(idx + 1).trim();
            if (k && v) pairs[k] = v;
        }
    });
    let filled = 0;
    if (pairs['uin']) { document.getElementById('tx_uin').value = pairs['uin']; filled++; }
    if (pairs['qqmusic_key'] || pairs['qm_keyst']) {
        document.getElementById('tx_token').value = pairs['qqmusic_key'] || pairs['qm_keyst']; filled++;
    }
    if (pairs['psrf_qqrefresh_token']) { document.getElementById('tx_refreshkey').value = pairs['psrf_qqrefresh_token']; filled++; }
    document.getElementById('tx_parse_hint').textContent =
        filled > 0 ? `已自动提取 ${filled} 个字段（uin/token/refreshKey），确认无误后点保存` : '未识别到有效字段，请检查 Cookie 格式';
}
</script>
</body>
</html>
