<?php

header('Content-Type: text/plain; charset=utf-8');

$configFile = __DIR__ . '/config.json';
$logFile = __DIR__ . '/logs/migu_refresh.log';
$logDir = dirname($logFile);
if (!is_dir($logDir)) mkdir($logDir, 0775, true);

function logMsg($msg) {
    global $logFile;
    $line = "[" . date('Y-m-d H:i:s') . "] " . $msg . "\n";
    file_put_contents($logFile, $line, FILE_APPEND);
    echo $line;
}

if (!file_exists($configFile)) {
    logMsg("错误: config.json 不存在");
    exit(1);
}

$config = json_decode(file_get_contents($configFile), true);
$miguCfg = $config['migu'] ?? [];
$phone = $miguCfg['accounts'][0] ?? '';
$password = $miguCfg['password'] ?? '';

if (!$phone || !$password) {
    logMsg("错误: 咪咕账号或密码未配置，请先在 admin.php 中设置");
    exit(1);
}

$refreshHours = $miguCfg['auto_refresh_hours'] ?? 3;
$lastRefresh = $miguCfg['last_refresh'] ?? '';
if ($lastRefresh) {
    $lastTime = strtotime($lastRefresh);
    $now = time();
    if ($now - $lastTime < $refreshHours * 3600 - 60) {
        $remaining = ceil(($refreshHours * 3600 - ($now - $lastTime)) / 60);
        logMsg("跳过: 距上次刷新不足 {$refreshHours} 小时，还剩 {$remaining} 分钟");
        exit(0);
    }
}

logMsg("开始刷新咪咕凭证: {$phone}");

$cmd = sprintf('php %s/migu_login.php %s %s 2>&1',
    escapeshellarg(__DIR__),
    escapeshellarg($phone),
    escapeshellarg($password)
);
exec($cmd, $output, $retCode);
$result = implode("\n", $output);

if ($retCode === 0 && strpos($result, '流程完成') !== false) {
    $config['migu']['last_refresh'] = date('Y-m-d H:i:s');
    file_put_contents($configFile, json_encode($config, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    logMsg("✅ 咪咕凭证刷新成功");
} else {
    logMsg("❌ 咪咕凭证刷新失败 (exit={$retCode})");
    logMsg("输出: " . substr($result, -300));
    exit(1);
}
