<?php
/**
 * 全平台凭证自动刷新 - Cron入口
 * 刷新: 酷狗 / QQ音乐 / 咪咕
 * 网易云Cookie有效期较长，仅检查不强制刷新
 *
 * 用法: curl -s "http://你的域名/refresh_cron.php"
 * 或: php refresh_cron.php
 */
header('Content-Type: text/plain; charset=utf-8');

$ROOT = __DIR__;
$configFile = $ROOT . '/config.json';
$logFile = $ROOT . '/logs/refresh.log';
$logDir = dirname($logFile);
if (!is_dir($logDir)) mkdir($logDir, 0775, true);

function logMsg($msg) {
    global $logFile;
    $line = "[" . date('Y-m-d H:i:s') . "] " . $msg . "\n";
    file_put_contents($logFile, $line, FILE_APPEND);
    echo $line;
}

/**
 * 从 exec 输出中健壮解析 JSON（兼容日志行混在输出中）
 */
function parse_python_output($outputLines) {
    $data = null;
    // 从最后一行往前找以 { 开头的可解析 JSON
    for ($i = count($outputLines) - 1; $i >= 0; $i--) {
        $line = trim($outputLines[$i]);
        if ($line === '' || $line[0] !== '{') continue;
        $decoded = json_decode($line, true);
        if (is_array($decoded)) { $data = $decoded; break; }
    }
    // 兜底：贪婪匹配整段中的 JSON
    if ($data === null) {
        $text = implode("\n", $outputLines);
        if (preg_match('/\{[\s\S]*\}/', $text, $m)) {
            $decoded = json_decode($m[0], true);
            if (is_array($decoded)) $data = $decoded;
        }
    }
    return $data;
}

function run_python_refresh($scriptArgs) {
    global $PYTHON, $ROOT;
    $cmd = escapeshellcmd($PYTHON) . ' ' . escapeshellarg($ROOT . '/py/kg_tx.py') . ' ' . implode(' ', array_map('escapeshellarg', $scriptArgs)) . ' 2>&1';
    exec($cmd, $out, $ret);
    $data = parse_python_output($out);
    return ['ret' => $ret, 'data' => $data, 'raw' => implode("\n", $out)];
}

if (!file_exists($configFile)) {
    logMsg("错误: config.json 不存在");
    exit(1);
}

$config = json_decode(file_get_contents($configFile), true);

// 检测Python路径
$venvPython = $ROOT . '/venv/bin/python3';
$PYTHON = (file_exists($venvPython) && is_executable($venvPython)) ? $venvPython : 'python3';

$results = [];
$hasError = false;

logMsg("========== 开始全平台凭证刷新 ==========");

// ---------- 1. 酷狗 ----------
$kgUsers = $config['modules']['platform']['kg']['users'] ?? [];
$kgConfigured = !empty($kgUsers[0]['token']);
if ($kgConfigured) {
    logMsg("[酷狗] 开始刷新...");
    $r = run_python_refresh(['kg', 'refresh']);
    $data = $r['data'];
    if ($r['ret'] === 0 && $data && isset($data['code']) && $data['code'] === 200) {
        logMsg("[酷狗] ✓ 刷新成功");
        if (isset($data['users'])) {
            $config['modules']['platform']['kg']['users'] = $data['users'];
        }
        $results['kg'] = 'OK';
    } else {
        logMsg("[酷狗] ✗ 刷新失败: " . substr($r['raw'], -200));
        $results['kg'] = 'FAIL';
        $hasError = true;
    }
} else {
    logMsg("[酷狗] 跳过（未配置）");
    $results['kg'] = 'SKIP';
}

// ---------- 2. QQ音乐 ----------
$txUsers = $config['modules']['platform']['tx']['users'] ?? [];
$txConfigured = !empty($txUsers[0]['token']);
if ($txConfigured) {
    logMsg("[QQ音乐] 开始刷新...");
    $r = run_python_refresh(['tx', 'refresh']);
    $data = $r['data'];
    if ($r['ret'] === 0 && $data && isset($data['code']) && $data['code'] === 200) {
        logMsg("[QQ音乐] ✓ 刷新成功");
        if (isset($data['users'])) {
            $config['modules']['platform']['tx']['users'] = $data['users'];
        }
        $results['tx'] = 'OK';
    } else {
        logMsg("[QQ音乐] ✗ 刷新失败: " . substr($r['raw'], -200));
        $results['tx'] = 'FAIL';
        $hasError = true;
    }
} else {
    logMsg("[QQ音乐] 跳过（未配置）");
    $results['tx'] = 'SKIP';
}

// ---------- 3. 咪咕 ----------
$miguPhone = $config['migu']['accounts'][0] ?? '';
$miguPassword = $config['migu']['password'] ?? '';
$refreshHours = $config['migu']['auto_refresh_hours'] ?? 3;
$lastRefresh = $config['migu']['last_refresh'] ?? '';

$needRefresh = true;
if ($lastRefresh) {
    $elapsed = time() - strtotime($lastRefresh);
    if ($elapsed < $refreshHours * 3600 - 60) {
        $needRefresh = false;
        logMsg("[咪咕] 距上次刷新不足 {$refreshHours} 小时，跳过");
        $results['migu'] = 'SKIP';
    }
}

if ($miguPhone && $miguPassword && $needRefresh) {
    logMsg("[咪咕] 开始刷新: {$miguPhone}");
    $cmd = sprintf('php %s/migu_login.php %s %s 2>&1',
        escapeshellarg($ROOT),
        escapeshellarg($miguPhone),
        escapeshellarg($miguPassword)
    );
    exec($cmd, $out, $ret);
    $output = implode("\n", $out);
    if ($ret === 0 && strpos($output, '流程完成') !== false) {
        $config['migu']['last_refresh'] = date('Y-m-d H:i:s');
        logMsg("[咪咕] ✓ 刷新成功");
        $results['migu'] = 'OK';
    } else {
        logMsg("[咪咕] ✗ 刷新失败: " . substr($output, -200));
        $results['migu'] = 'FAIL';
        $hasError = true;
    }
} elseif (!$miguPhone || !$miguPassword) {
    logMsg("[咪咕] 跳过（未配置账号密码）");
    $results['migu'] = 'SKIP';
}

// ---------- 保存更新后的config ----------
file_put_contents($configFile, json_encode($config, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));

// ---------- 汇总 ----------
logMsg("========== 刷新汇总 ==========");
foreach ($results as $platform => $status) {
    $icon = $status === 'OK' ? '✓' : ($status === 'SKIP' ? '○' : '✗');
    logMsg("  $platform: $icon $status");
}

if ($hasError) {
    logMsg("部分平台刷新失败，请检查日志: $logFile");
    exit(1);
}

logMsg("========== 全部完成 ==========");
