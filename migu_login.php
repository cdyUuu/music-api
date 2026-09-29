<?php

$configFile = __DIR__ . '/config.json';
$config = file_exists($configFile) ? json_decode(file_get_contents($configFile), true) : [];
$miguCfg = $config['migu'] ?? [];

if (isset($argv[1])) $account = $argv[1];
elseif (isset($miguCfg['accounts'][0])) $account = $miguCfg['accounts'][0];
else { echo "错误: 请提供手机号（命令行参数或config.json）\n"; exit(1); }

if (isset($argv[2])) $password = $argv[2];
elseif (isset($miguCfg['password'])) $password = $miguCfg['password'];
else $password = '';

if (isset($argv[3])) $sourceID = $argv[3];
elseif (isset($miguCfg['sourceID'])) $sourceID = $miguCfg['sourceID'];
else $sourceID = '220029';

if (!$password) {
    echo "错误: 未设置咪咕密码，请在 admin.php 中配置或使用命令行参数\n";
    echo "用法: php migu_login.php 手机号 密码 [sourceID]\n";
    exit(1);
}

$deviceId = 'web_' . substr(md5(time() . rand(1000, 9999)), 0, 16);

$dataDir = __DIR__ . '/data';
if (!is_dir($dataDir)) @mkdir($dataDir, 0775, true);

$cookieFile = $dataDir . '/cookies_' . $account . '.txt';
$resultFile = $dataDir . '/cookies_' . $account . '.json';
$loginPageUrl = "https://passport.migu.cn/login?sourceid={$sourceID}&callbackURL=PostToken";
$userAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

function curlRequest($url, $method = 'GET', $postData = null, $headers = [], $follow = true) {
    global $cookieFile, $userAgent;
    $ch = curl_init();
    curl_setopt_array($ch, [
        CURLOPT_URL => $url,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_SSL_VERIFYPEER => false,
        CURLOPT_SSL_VERIFYHOST => false,
        CURLOPT_COOKIEJAR => $cookieFile,
        CURLOPT_COOKIEFILE => $cookieFile,
        CURLOPT_USERAGENT => $userAgent,
        CURLOPT_ENCODING => 'gzip, deflate',
        CURLOPT_FOLLOWLOCATION => $follow,
        CURLOPT_TIMEOUT => 30,
    ]);
    if ($method === 'POST') {
        curl_setopt($ch, CURLOPT_POST, true);
        if ($postData !== null) curl_setopt($ch, CURLOPT_POSTFIELDS, $postData);
    }
    if (!empty($headers)) curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
    $response = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return ['body' => $response, 'code' => $httpCode];
}

function derLength($length) {
    if ($length < 0x80) return chr($length);
    elseif ($length < 0x100) return chr(0x81) . chr($length);
    else return chr(0x82) . chr(($length >> 8) & 0xFF) . chr($length & 0xFF);
}

function derInteger($hexStr) {
    $bytes = hex2bin($hexStr);
    if (ord($bytes[0]) & 0x80) $bytes = "\x00" . $bytes;
    return "\x02" . derLength(strlen($bytes)) . $bytes;
}

function derSequence($content) {
    return "\x30" . derLength(strlen($content)) . $content;
}

function derBitString($content) {
    return "\x03" . derLength(strlen($content) + 1) . "\x00" . $content;
}

function buildRsaPublicKey($modulusHex, $exponentHex) {
    $rsaOid = hex2bin('06092a864886f70d010101');
    $null = hex2bin('0500');
    $algId = derSequence($rsaOid . $null);
    $rsaPubKey = derSequence(derInteger($modulusHex) . derInteger($exponentHex));
    $spki = derSequence($algId . derBitString($rsaPubKey));
    $pem = "-----BEGIN PUBLIC KEY-----\n";
    $pem .= wordwrap(base64_encode($spki), 64, "\n", true);
    $pem .= "\n-----END PUBLIC KEY-----\n";
    return $pem;
}

function rsaEncryptHex($plaintext, $modulusHex, $exponentHex) {
    $publicKeyPem = buildRsaPublicKey($modulusHex, $exponentHex);
    $publicKey = openssl_pkey_get_public($publicKeyPem);
    if (!$publicKey) throw new Exception('公钥解析失败: ' . openssl_error_string());
    $encrypted = '';
    if (!openssl_public_encrypt($plaintext, $encrypted, $publicKey, OPENSSL_PKCS1_PADDING))
        throw new Exception('RSA加密失败: ' . openssl_error_string());
    openssl_free_key($publicKey);
    return bin2hex($encrypted);
}

header('Content-Type: text/plain; charset=utf-8');
echo "=== 咪咕音乐自动登录 ===\n\n";

if (file_exists($cookieFile)) @unlink($cookieFile);

try {
    echo "[步骤0] 获取登录页会话 Cookie...\n";
    $result = curlRequest($loginPageUrl, 'GET');
    echo "  HTTP状态: {$result['code']}\n\n";

    echo "[步骤1] 请求加密公钥...\n";
    $pubkeyUrl = 'https://passport.migu.cn/password/publickey';
    $headers = ['Referer: ' . $loginPageUrl, 'Accept: application/json, text/javascript, **; q=0.01', 'X-Requested-With: XMLHttpRequest'];
    $result = curlRequest($authnUrl, 'POST', $postData, $headers, false);
    echo "  HTTP状态: {$result['code']}\n";
    $loginData = json_decode($result['body'], true);
    if (!$loginData) throw new Exception("登录响应解析失败");
    if (isset($loginData['status']) && $loginData['status'] != 2000) {
        $msg = $loginData['message'] ?? '未知错误';
        throw new Exception("登录失败: {$msg}");
    }
    $token = $loginData['result']['token'] ?? '';
    if (!$token) throw new Exception("登录响应中未找到 token");
    echo "  登录成功! Token: " . substr($token, 0, 30) . "...\n\n";

    echo "[步骤3] 验证 token 换取业务会话...\n";
    $mtokenUrl = 'https://c.musicapp.migu.cn/MIGUM3.0/user/h5/token-validate/v2.0?token=' . urlencode($token) . '&type=2';
    $headers = ['Referer: https://music.migu.cn/', 'deviceId: ' . $deviceId,
        'channel: 0140000A', 'subchannel: 00000000', 'Accept: application/json, text/plain, **',
        'deviceId: ' . $deviceId, 'channel: 0140000A', 'subchannel: 00000000'];
    if ($usessionId) $headers[] = 'mtoken: ' . $usessionId;
    $result = curlRequest($userInfoUrl, 'GET', null, $headers);
    echo "  HTTP状态: {$result['code']}\n\n";
    $userInfo = json_decode($result['body'], true);

    echo "[保存] 写入凭证文件...\n";
    $output = [
        'update_time' => date('Y-m-d H:i:s'),
        'account' => $account,
        'device_id' => $deviceId,
        'user_id' => $userId,
        'login_token' => $token,
        'mtoken' => $usessionId,
        'usession_id' => $usessionId,
    ];
    file_put_contents($resultFile, json_encode($output, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    echo "  凭证: {$resultFile}\n";
    echo "  Cookie: {$cookieFile}\n\n";

    echo "=== 完成 ===\n";
    echo "登录Token:  " . ($token ? '✓' : '✗') . "\n";
    echo "usessionId: " . ($usessionId ? '✓' : '✗') . "\n";

} catch (Exception $e) {
    echo "\n[错误] " . $e->getMessage() . "\n";
}
