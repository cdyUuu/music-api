<?php

$dataDir = __DIR__ . '/data';
if (!is_dir($dataDir)) mkdir($dataDir, 0755, true);

$keyFile = $dataDir . '/admin_key.txt';

$key = bin2hex(random_bytes(16));
file_put_contents($keyFile, $key);

$protocol = isset($_SERVER['HTTPS']) && $_SERVER['HTTPS'] === 'on' ? 'https' : 'http';
$host = $_SERVER['HTTP_HOST'] ?? 'your-domain.com';
$path = dirname($_SERVER['PHP_SELF'] ?? '/');
$url = "{$protocol}://{$host}{$path}/admin.php?key={$key}";

echo "========================================\n";
echo "  管理后台密钥已生成\n";
echo "========================================\n\n";
echo "密钥: {$key}\n\n";
echo "访问地址:\n";
echo "  {$url}\n\n";
echo "密钥已保存到: data/admin_key.txt\n";
echo "请妥善保管，不要泄露给他人\n";
echo "========================================\n";
