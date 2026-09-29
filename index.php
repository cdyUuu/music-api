<?php
/**
 * 统一音乐 API 入口 v2
 * 支持: kg(酷狗) / tx(QQ) / wy(网易) / kw(酷我) / migu(咪咕)
 * 兼容 LX Music API 格式
 *
 * API:
 *   GET /url?source=kg&songId=xxx&quality=flac
 *   GET /info?source=kg&songId=xxx
 *   GET /lyric?source=kg&songId=xxx
 */

header('Content-Type: application/json; charset=utf-8');

$CONFIG = json_decode(@file_get_contents(__DIR__ . '/config.json'), true) ?: [];
$SEC = $CONFIG['security'] ?? [];

// CORS白名单
if (!empty($SEC['cors_enabled'])) {
    $origin = $_SERVER['HTTP_ORIGIN'] ?? '';
    $allowed = $SEC['cors_origins'] ?? ['*'];
    if (in_array('*', $allowed) || in_array($origin, $allowed)) {
        header('Access-Control-Allow-Origin: ' . ($origin ?: '*'));
        header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
        header('Access-Control-Allow-Headers: Content-Type');
    }
} else {
    header('Access-Control-Allow-Origin: *');
    header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
}

$_SERVER['REQUEST_METHOD'] = $_SERVER['REQUEST_METHOD'] ?? 'GET';
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(200);
    exit;
}

// API Key鉴权
if (!empty($SEC['api_key_enabled']) && !empty($SEC['api_key'])) {
    $key = $_GET['key'] ?? $_SERVER['HTTP_X_API_KEY'] ?? '';
    if (!hash_equals($SEC['api_key'], $key)) {
        http_response_code(403);
        echo json_encode(['code' => 403, 'message' => 'API Key无效或缺失'], JSON_UNESCAPED_UNICODE);
        exit;
    }
}

// ============================================================
// 路径与配置
// ============================================================
$ROOT = __DIR__;
$PY_DIR = $ROOT . '/py';
$CACHE_DIR = $ROOT . '/cache';
$LOG_DIR = $ROOT . '/logs';
$DATA_DIR = $ROOT . '/data';
$CONFIG_FILE = $ROOT . '/config.json';

foreach ([$CACHE_DIR, $LOG_DIR, $DATA_DIR] as $d) {
    if (!is_dir($d)) {
        @mkdir($d, 0775, true);
    }
    if (is_dir($d) && !is_writable($d)) {
        @chmod($d, 0775);
    }
}

$CONFIG = json_decode(@file_get_contents($CONFIG_FILE), true) ?: [];

// Python路径：优先项目venv，其次环境变量，最后系统python3
$VENV_PYTHON = $ROOT . '/venv/bin/python3';
if (file_exists($VENV_PYTHON) && is_executable($VENV_PYTHON)) {
    $PYTHON = $VENV_PYTHON;
} else {
    $PYTHON = getenv('PYTHON_BIN') ?: 'python3';
}

// ============================================================
// 统一日志
// ============================================================
function app_log($tag, $msg) {
    global $LOG_DIR;
    $line = '[' . date('Y-m-d H:i:s') . '] [' . $tag . '] ' . $msg . PHP_EOL;
    $logFile = $LOG_DIR . '/music-api_' . date('Y-m-d') . '.log';
    if (!is_dir($LOG_DIR)) {
        @mkdir($LOG_DIR, 0775, true);
    }
    if (is_writable($LOG_DIR) || (!file_exists($logFile) && is_writable($LOG_DIR))) {
        @file_put_contents($logFile, $line, FILE_APPEND | LOCK_EX);
    } else {
        error_log("[music-api][$tag] $msg");
    }
    // 概率清理7天前的旧日志（1%概率，避免每次扫描）
    if (rand(1, 100) === 1 && is_dir($LOG_DIR)) {
        $cutoff = time() - 7 * 86400;
        foreach (glob($LOG_DIR . '/music-api_*.log') as $f) {
            if (filemtime($f) < $cutoff) @unlink($f);
        }
    }
}

// ============================================================
// 缓存
// ============================================================
function get_cache($key, $ttl = 3600) {
    global $CACHE_DIR;
    $file = $CACHE_DIR . '/' . md5($key) . '.json';
    if (file_exists($file) && (time() - filemtime($file)) < $ttl) {
        $data = @file_get_contents($file);
        if ($data) return json_decode($data, true);
    }
    return null;
}

function set_cache($key, $data) {
    global $CACHE_DIR;
    $file = $CACHE_DIR . '/' . md5($key) . '.json';
    @file_put_contents($file, json_encode($data, JSON_UNESCAPED_UNICODE), LOCK_EX);
    // 概率清理过期缓存（1%概率，缓存TTL最长86400秒）
    if (rand(1, 100) === 1 && is_dir($CACHE_DIR)) {
        $cutoff = time() - 86400;
        foreach (glob($CACHE_DIR . '/*.json') as $f) {
            if (filemtime($f) < $cutoff) @unlink($f);
        }
    }
}

// ============================================================
// 调用Python脚本
// ============================================================
// ============================================================
// Python 常驻服务（Unix Socket）调用
// ============================================================
define('PY_SOCKET', '/run/music-api/music-api.sock');

function python_socket_available() {
    static $available = null;
    if ($available === null) {
        $available = file_exists(PY_SOCKET) && is_writable(PY_SOCKET);
    }
    return $available;
}

/**
 * 将 run_python 的参数映射为常驻服务的 URL 路径和查询参数
 * 返回 [path, query_array] 或 null（不支持的调用走 exec fallback）
 */
function map_python_call($script, $args) {
    global $nocache;
    $nc = $nocache ? 1 : 0;
    if ($script === 'kg_tx.py') {
        $source = $args[0] ?? '';
        $action = $args[1] ?? '';
        if ($action === 'refresh') {
            return ['/refresh', ['source' => $source]];
        }
        $songId = $args[2] ?? '';
        $quality = $args[3] ?? '320k';
        if ($action === 'url') {
            return ['/url', ['source' => $source, 'songId' => $songId, 'quality' => $quality, 'nocache' => $nc]];
        } elseif ($action === 'info') {
            return ['/info', ['source' => $source, 'songId' => $songId]];
        } elseif ($action === 'lyric') {
            return ['/lyric', ['source' => $source, 'songId' => $songId]];
        }
    } elseif ($script === 'wy.py') {
        $action = $args[0] ?? '';
        $songId = $args[1] ?? '';
        $quality = $args[2] ?? '320k';
        if ($action === 'url') {
            return ['/url', ['source' => 'wy', 'songId' => $songId, 'quality' => $quality, 'nocache' => $nc]];
        } elseif ($action === 'info') {
            return ['/info', ['source' => 'wy', 'songId' => $songId]];
        } elseif ($action === 'lyric') {
            return ['/lyric', ['source' => 'wy', 'songId' => $songId]];
        }
    } elseif ($script === 'kw_vip.py') {
        // kw_vip.py args: [songId, quality, '--json']
        $songId = $args[0] ?? '';
        $quality = $args[1] ?? 'flac';
        return ['/url', ['source' => 'kw', 'songId' => $songId, 'quality' => $quality, 'nocache' => $nc]];
    }
    return null;
}

function call_python_socket($script, $args, $timeout = 15) {
    $mapped = map_python_call($script, $args);
    if ($mapped === null) return null;
    list($path, $query) = $mapped;
    $url = 'http://localhost' . $path . '?' . http_build_query($query);

    $ch = curl_init();
    curl_setopt_array($ch, [
        CURLOPT_URL => $url,
        CURLOPT_UNIX_SOCKET_PATH => PY_SOCKET,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => $timeout,
        CURLOPT_CONNECTTIMEOUT => 2,
    ]);
    $body = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $errno = curl_errno($ch);
    curl_close($ch);

    if ($errno !== 0 || $body === false) {
        return null; // 连接失败，走 fallback
    }
    $data = json_decode($body, true);
    if (!is_array($data)) {
        app_log('PY_SOCKET', "非JSON响应: " . substr($body, 0, 200));
        return null;
    }
    return $data;
}

function run_python($script, $args, $timeout = 15) {
    // 优先走常驻服务（Unix Socket）
    if (python_socket_available()) {
        $result = call_python_socket($script, $args, $timeout);
        if ($result !== null) {
            return $result;
        }
        app_log('PY_SOCKET', "socket调用失败，回退exec: $script " . implode(' ', $args));
    }

    // Fallback: 传统 exec 方式
    global $PYTHON, $PY_DIR;
    $cmd = escapeshellcmd($PYTHON) . ' ' . escapeshellarg($PY_DIR . '/' . $script);
    foreach ($args as $arg) {
        $cmd .= ' ' . escapeshellarg($arg);
    }
    $cmd .= ' 2>&1';
    $output = [];
    $retCode = 0;
    exec("timeout $timeout $cmd", $output, $retCode);
    $text = implode("\n", $output);
    // 优先：从最后一行往前找可解析的JSON（脚本结果在末行，日志行不会以{开头）
    $data = null;
    for ($i = count($output) - 1; $i >= 0; $i--) {
        $line = trim($output[$i]);
        if ($line === '' || $line[0] !== '{') continue;
        $decoded = json_decode($line, true);
        if (is_array($decoded)) { $data = $decoded; break; }
    }
    // 兜底：贪婪匹配整段输出中的JSON（兼容多行JSON输出）
    if ($data === null && preg_match('/\{[\s\S]*\}/', $text, $m)) {
        $decoded = json_decode($m[0], true);
        if (is_array($decoded)) $data = $decoded;
    }
    if ($data !== null) return $data;
    app_log('PY_ERROR', "script=$script ret=$retCode out=" . substr($text, 0, 500));
    global $SEC;
    if (empty($SEC['hide_errors'])) {
        return ['code' => 500, 'message' => 'Python脚本执行失败', 'raw' => $text];
    }
    return ['code' => 500, 'message' => '服务器内部错误，请查看日志'];
}

// ============================================================
// 风控检测
// ============================================================
function is_risk_control($result) {
    $riskCodes = [104003, 104004, 3001, 3002, 3010, 3020];
    $code = $result['code'] ?? ($result['error_code'] ?? null);
    if (in_array($code, $riskCodes, true)) return true;
    $msg = ($result['message'] ?? '') . ' ' . ($result['msg'] ?? '');
    $riskKeywords = ['人机', '验证', 'captcha', '封禁', '风控', '限制', 'risk', 'verify', 'unusual', 'forbidden', 'ip被', '账号被', '安全验证', '滑块'];
    $msgLower = strtolower($msg);
    foreach ($riskKeywords as $kw) {
        if (strpos($msgLower, strtolower($kw)) !== false) return true;
    }
    return false;
}

function risk_message($source) {
    $names = ['kg' => '酷狗', 'tx' => 'QQ音乐'];
    $name = $names[$source] ?? $source;
    return "$name 触发了人机验证/风控，自动刷新无法解除。请在手机/设备上登录对应账号完成验证，或在官方APP中正常使用几分钟后再试；期间请勿频繁请求以免加重限制。";
}

// ============================================================
// 酷我音乐 (PHP原生获取info/lyric)
// ============================================================
function kw_get_info($rid) {
    if (!$rid) return null;
    $url = 'http://m.kuwo.cn/newh5/singles/songinfoandlrc?' . http_build_query(['musicId' => $rid, 'httpsStatus' => '1']);
    $ch = curl_init();
    curl_setopt_array($ch, [
        CURLOPT_URL => $url,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 10,
        CURLOPT_SSL_VERIFYPEER => false,
        CURLOPT_SSL_VERIFYHOST => false,
        CURLOPT_USERAGENT => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    ]);
    $body = curl_exec($ch);
    curl_close($ch);
    if (!$body) return null;
    $data = json_decode($body, true);
    $songinfo = $data['data']['songinfo'] ?? [];
    $lrclist = $data['data']['lrclist'] ?? [];
    $lrc = '';
    if ($lrclist) {
        foreach ($lrclist as $line) {
            $time = floatval($line['time'] ?? 0);
            $m = floor($time / 60);
            $s = $time - $m * 60;
            $lrc .= sprintf('[%02d:%05.2f]%s', $m, $s, $line['lineLyric'] ?? '') . "\n";
        }
    }
    return [
        'songName' => $songinfo['songName'] ?? '',
        'artist' => $songinfo['artist'] ?? '',
        'album' => $songinfo['album'] ?? '',
        'pic' => $songinfo['pic'] ?? '',
        'lrc' => $lrc,
    ];
}

// ============================================================
// 咪咕音乐 (PHP原生)
// ============================================================

/**
 * 从咪咕播放 URL 中解析实际音质
 * 咪咕 toneFlag: PQ=128k / HQ=320k / SQ=flac / ZQ=hires|flac24bit
 * 也可从 URL 路径段（/ZQ/、/SQ/、/HQ/、/PQ/）识别
 */
function migu_parse_quality_from_url($url) {
    if (!$url) return '';
    $u = strtolower($url);
    if (strpos($u, '/zq/') !== false || strpos($u, 'zq/') !== false) {
        // ZQ 在咪咕对应 hires/flac24bit，URL 中无法区分，统一报 hires
        return 'hires';
    }
    if (strpos($u, '/sq/') !== false || strpos($u, 'sq/') !== false || strpos($u, '/flac/') !== false) {
        return 'flac';
    }
    if (strpos($u, '/hq/') !== false || strpos($u, 'hq/') !== false) {
        return '320k';
    }
    if (strpos($u, '/pq/') !== false || strpos($u, 'pq/') !== false) {
        return '128k';
    }
    // 兜底用扩展名
    if (substr($u, -5) === '.flac') return 'flac';
    if (substr($u, -4) === '.mp3') return '320k';
    return '';
}

/**
 * 比较请求音质与实际音质，返回 match/fallback/upgraded
 */
function migu_compare_quality($requested, $actual) {
    $rank = [
        '128k' => 1, '192k' => 2, '320k' => 3,
        'flac' => 4, 'flac24bit' => 5,
        'hires' => 6, 'master' => 9,
    ];
    $r = $rank[$requested] ?? 0;
    $a = $rank[$actual] ?? 0;
    if ($a === $r) return 'match';
    return $a < $r ? 'fallback' : 'upgraded';
}

class MiguAPI {
    private $accounts;
    private $decryptKey;
    private $miguConfig = [];
    private $debugLogs = [];

    public function __construct($config) {
        $accounts = $config['migu']['accounts'] ?? [];
        $this->accounts = array_values(array_filter($accounts, function($a) { return !empty($a); }));
        if (empty($this->accounts)) $this->accounts = [''];
        $this->decryptKey = $config['migu']['decrypt_key'] ?? 'Jk8qzuePiJ1qE3mDYhLQ3T73DtDoAhLP';
        $this->miguConfig = $config['migu'] ?? [];
    }

    public function autoLogin() {
        global $DATA_DIR, $ROOT;
        $password = $this->miguConfig['password'] ?? '';
        if (!$password) return;
        foreach ($this->accounts as $account) {
            if (!$account) continue;
            $tokenFile = $DATA_DIR . '/cookies_' . $account . '.json';
            if (file_exists($tokenFile)) continue;
            $lockFile = $DATA_DIR . '/migu_login_' . $account . '.lock';
            if (file_exists($lockFile) && (time() - filemtime($lockFile)) < 60) continue;
            @touch($lockFile);
            $loginScript = $ROOT . '/migu_login.php';
            if (file_exists($loginScript)) {
                $cmd = 'php ' . escapeshellarg($loginScript) . ' ' . escapeshellarg($account) . ' ' . escapeshellarg($password) . ' 2>&1';
                exec($cmd, $out, $ret);
                app_log('MIGU_AUTOLOGIN', "account=$account ret=$ret out=" . implode(' ', array_slice($out, -3)));
            }
            @unlink($lockFile);
        }
    }

    private function addLog($msg) {
        $this->debugLogs[] = ['time' => date('Y-m-d H:i:s'), 'msg' => $msg];
        app_log('MIGU', $msg);
    }

    private function getAccountFiles($account) {
        global $DATA_DIR;
        return [
            'cookie' => $DATA_DIR . '/cookies_' . $account . '.txt',
            'token'  => $DATA_DIR . '/cookies_' . $account . '.json',
        ];
    }

    private function loadToken($account) {
        $files = $this->getAccountFiles($account);
        if (!file_exists($files['token'])) return null;
        $data = json_decode(@file_get_contents($files['token']), true);
        return $data['usession_id'] ?? $data['mtoken'] ?? null;
    }

    private function loadLoginToken($account) {
        $files = $this->getAccountFiles($account);
        if (!file_exists($files['token'])) return null;
        $data = json_decode(@file_get_contents($files['token']), true);
        return $data['login_token'] ?? null;
    }

    private function curlGet($url, $headers, $cookieFile, $timeout = 10) {
        $ch = curl_init();
        curl_setopt_array($ch, [
            CURLOPT_URL => $url,
            CURLOPT_HTTPHEADER => $headers,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => $timeout,
            CURLOPT_SSL_VERIFYPEER => false,
            CURLOPT_SSL_VERIFYHOST => false,
            CURLOPT_FOLLOWLOCATION => true,
            CURLOPT_ENCODING => '',
            CURLOPT_COOKIEFILE => $cookieFile,
            CURLOPT_COOKIEJAR => $cookieFile,
        ]);
        $body = curl_exec($ch);
        $err = curl_error($ch);
        curl_close($ch);
        if ($err) {
            $this->addLog("CURL error: $err");
            return null;
        }
        return $body;
    }

    private function buildHeaders($token = null) {
        $h = [
            'Content-Type: application/json;charset=UTF-8',
            'birth: h5page', 'signature: 1',
            'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Origin: https://h5.nf.migu.cn', 'Referer: https://h5.nf.migu.cn/',
            'ua: Android_migu', 'version: 6.8.8', 'channel: 014021I', 'Accept: */*',
        ];
        if ($token) $h[] = "mtoken: $token";
        return $h;
    }

    private function miguDecrypt($raw) {
        $len = strlen($raw);
        if ($len < 4 || ord($raw[0]) !== 0xab || ord($raw[1]) !== 0xcd || ord($raw[2]) !== 0x01) return $raw;
        $seed = ord($raw[3]);
        $key = $this->decryptKey;
        $out = '';
        for ($i = 0, $j = 4; $j < $len; $i++, $j++) {
            $out .= chr((ord($raw[$j]) + $seed - ord($key[$i % strlen($key)])) & 0xFF);
        }
        return $out;
    }

    private function searchSong($keyword, $loginToken) {
        $params = http_build_query([
            'text' => $keyword, 'pageNo' => '1', 'pageSize' => '5',
            'searchSwitch' => '{"song":1,"album":0,"singer":0,"tagSong":0,"mvSong":0,"songlist":0,"bestShow":0}',
        ]);
        $url = "https://app.c.nf.migu.cn/MIGUM2.0/v1.0/content/search_all.do?$params";
        $h = ['User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'ua: Android_migu', 'version: 5.1'];
        if ($loginToken) $h[] = "loginToken: $loginToken";
        $tmpCookie = tempnam(sys_get_temp_dir(), 'migu_');
        $body = $this->curlGet($url, $h, $tmpCookie);
        @unlink($tmpCookie);
        if (!$body) { $this->addLog("SEARCH | 请求失败"); return null; }
        $data = json_decode($body, true);
        $songs = $data['songResultData']['result'] ?? [];
        if (!$songs) { $this->addLog("SEARCH | 无结果, 返回=" . substr($body, 0, 100)); return null; }
        $first = $songs[0];
        return [
            'contentId' => $first['contentId'] ?? '',
            'copyrightId' => $first['copyrightId'] ?? '',
            'name' => $first['name'] ?? '',
            'singer' => implode(',', array_column($first['singers'] ?? [], 'name')),
        ];
    }

    private function fetchLrcContent($lrcUrl) {
        if (!$lrcUrl || !preg_match('/^https?:\/\//', $lrcUrl)) return $lrcUrl;
        $ch = curl_init();
        curl_setopt_array($ch, [
            CURLOPT_URL => $lrcUrl,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => 10,
            CURLOPT_SSL_VERIFYPEER => false,
            CURLOPT_SSL_VERIFYHOST => false,
            CURLOPT_USERAGENT => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            CURLOPT_FOLLOWLOCATION => true,
        ]);
        $body = curl_exec($ch);
        curl_close($ch);
        if ($body && strlen($body) > 10) {
            $this->addLog("LRC | 从URL获取歌词成功, 长度=" . strlen($body));
            return $body;
        }
        $this->addLog("LRC | 从URL获取歌词失败");
        return $lrcUrl;
    }

    private function getSongInfo($songId) {
        $url = "https://app.c.nf.migu.cn/MIGUM2.0/v2.0/content/querySongBySongId.do?" .
               http_build_query(['songId' => $songId]);
        $h = ['User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'ua: Android_migu', 'version: 6.8.8', 'Accept: */*'];
        $tmpCookie = tempnam(sys_get_temp_dir(), 'migu_');
        $body = $this->curlGet($url, $h, $tmpCookie);
        @unlink($tmpCookie);
        if (!$body) { $this->addLog("INFO | 请求失败 songId={$songId}"); return null; }
        $json = json_decode($body, true);
        if ($json && ($json['code'] ?? '') === '000000' && !empty($json['resource'][0])) {
            $r = $json['resource'][0];
            return [
                'contentId' => $r['contentId'] ?? null,
                'copyrightId' => $r['copyrightId'] ?? null,
                'name' => $r['songName'] ?? '',
                'singer' => implode(',', array_column($r['artists'] ?? $r['singer'] ?? [], 'name')),
                'lrc' => $r['lrcUrl'] ?? null,
                'picture' => $r['albumImgs'][0]['img'] ?? null,
            ];
        }
        $this->addLog("INFO | 返回无效 code=" . ($json['code'] ?? 'null') . " body=" . substr($body, 0, 100));
        return null;
    }

    private function getListenUrl($contentId, $copyrightId, $quality, $account) {
        if (!$account) { $this->addLog("URL | 账号为空"); return null; }
        $token = $this->loadToken($account);
        $this->addLog("URL | 账号 {$account} token=" . ($token ? '已加载' : '未找到'));
        $toneMap = ['128k' => 'PQ', '192k' => 'HQ', '320k' => 'HQ', 'flac' => 'SQ', 'flac24bit' => 'ZQ', 'hires' => 'ZQ', 'master' => 'ZQ'];
        $toneFlag = $toneMap[$quality] ?? 'PQ';
        $resourceType = in_array($toneFlag, ['SQ', 'ZQ']) ? 'E' : '2';
        $params = http_build_query([
            'contentId' => $contentId,
            'copyrightId' => $copyrightId ?: $contentId,
            'resourceType' => $resourceType,
            'netType' => '01', 'toneFlag' => $toneFlag, 'scene' => '',
            'lowerQualityContentId' => $contentId,
        ]);
        $url = "https://c.musicapp.migu.cn/strategy/listen-url/h5/v2.4?$params";
        $files = $this->getAccountFiles($account);
        if (!file_exists($files['cookie'])) {
            $this->addLog("URL | cookie文件不存在: {$files['cookie']}");
        }
        $body = $this->curlGet($url, $this->buildHeaders($token), $files['cookie']);
        if ($body === null) { $this->addLog("URL | 请求失败"); return null; }
        if (strlen($body) >= 4 && ord($body[0]) === 0xab && ord($body[1]) === 0xcd && ord($body[2]) === 0x01) {
            $body = $this->miguDecrypt($body);
        }
        $data = json_decode($body, true);
        if ($data) {
            $this->addLog("URL | 返回 code=" . ($data['code'] ?? 'null') . " url=" . (isset($data['data']['url']) ? substr($data['data']['url'], 0, 60) : 'null'));
        } else {
            $this->addLog("URL | 返回非JSON: " . substr($body, 0, 100));
        }
        return $data;
    }

    public function handle($params) {
        $this->debugLogs = [];
        $name = trim($params['name'] ?? '');
        $singer = trim($params['singer'] ?? '');
        $quality = trim($params['quality'] ?? '320k');
        $songmid = trim($params['songmid'] ?? '');
        $onlyMeta = ($params['action'] ?? 'url') !== 'url';

        $this->addLog("START | name={$name} singer={$singer} songmid={$songmid} quality={$quality}");
        $this->addLog("ACCOUNTS | " . implode(',', $this->accounts));

        $loginToken = null;
        foreach ($this->accounts as $acc) {
            $loginToken = $this->loadLoginToken($acc);
            if ($loginToken) break;
        }
        $this->addLog("LOGIN_TOKEN | " . ($loginToken ? '已加载' : '未找到（搜索可能受限）'));

        $contentId = ''; $copyrightId = ''; $lrc = null; $picture = null;
        $finalName = $name; $finalSinger = $singer;

        if ($songmid) {
            $songId = preg_replace('/\D/', '', $songmid);
            if ($songId !== '') {
                $this->addLog("MID | 尝试 songId={$songId}");
                $info = $this->getSongInfo($songId);
                if ($info && $info['contentId']) {
                    $contentId = $info['contentId'];
                    $copyrightId = $info['copyrightId'] ?: $contentId;
                    $lrc = $info['lrc']; $picture = $info['picture'];
                    if (empty($finalName)) $finalName = $info['name'];
                    if (empty($finalSinger)) $finalSinger = $info['singer'];
                    $this->addLog("MID | 成功 contentId={$contentId} name={$finalName}");
                } else {
                    $this->addLog("MID | songId查询失败，将尝试搜索");
                }
            } else {
                $this->addLog("MID | songmid提取数字失败");
            }
        }

        if (!$contentId && $finalName) {
            $keyword = $finalName . ($finalSinger ? ' ' . $finalSinger : '');
            $this->addLog("SEARCH | 关键词: {$keyword}");
            $si = $this->searchSong($keyword, $loginToken);
            if ($si) {
                $contentId = $si['contentId'];
                $copyrightId = $si['copyrightId'];
                if (empty($finalName)) $finalName = $si['name'];
                $this->addLog("SEARCH | 命中 contentId={$contentId} name={$si['name']}");
            } else {
                $this->addLog("SEARCH | 未找到歌曲");
            }
        }

        if ($contentId && (is_null($lrc) || is_null($picture) || !$copyrightId || empty($finalName) || empty($finalSinger))) {
            $this->addLog("INFO | 补全信息 contentId={$contentId}");
            $info2 = $this->getSongInfo($contentId);
            if ($info2) {
                if (is_null($lrc)) $lrc = $info2['lrc'];
                if (is_null($picture)) $picture = $info2['picture'];
                if (empty($finalName)) $finalName = $info2['name'];
                if (empty($finalSinger)) $finalSinger = $info2['singer'];
                if (!$copyrightId) $copyrightId = $info2['copyrightId'] ?: $contentId;
            }
        }

        if (!$contentId) {
            $this->addLog("FAIL | 无法获取contentId");
            return ['code' => 400, 'msg' => '无法解析到有效歌曲，请检查参数（需要name+singer或正确的咪咕songmid）', 'debug' => $this->debugLogs];
        }

        // lyric/info模式：不获取播放URL，直接返回信息
        if ($onlyMeta) {
            if ($lrc && preg_match('/^https?:\/\//', $lrc)) {
                $fetched = $this->fetchLrcContent($lrc);
                if ($fetched) $lrc = $fetched;
            }
            $this->addLog("META | 返回信息 name={$finalName} lrc长度=" . strlen($lrc ?? ''));
            return [
                'code' => 200,
                'url' => null,
                'lrc' => $lrc ?: '',
                'picture' => $picture,
                'name' => $finalName, 'singer' => $finalSinger, 'songmid' => $songmid,
            ];
        }

        $primary = $this->accounts[array_rand($this->accounts)];
        $other = null;
        if (count($this->accounts) > 1) {
            $other = ($primary === $this->accounts[0]) ? $this->accounts[1] : $this->accounts[0];
        }
        $this->addLog("URL | 主账号: {$primary}" . ($other ? " 备用: {$other}" : " (单账号)"));

        $data = $this->getListenUrl($contentId, $copyrightId, $quality, $primary);
        if (!$data || ($data['code'] ?? '') !== '000000' || empty($data['data']['url'])) {
            $this->addLog("URL | 主账号失败 code=" . ($data['code'] ?? 'null'));
            if ($other) {
                $this->addLog("URL | 切换备用账号 {$other}");
                $data = $this->getListenUrl($contentId, $copyrightId, $quality, $other);
            }
        }

        if (!$data || ($data['code'] ?? '') !== '000000' || empty($data['data']['url'])) {
            $this->addLog("FAIL | 播放链接获取失败");
            return ['code' => 500, 'msg' => '获取播放链接失败（可能凭证过期，请重新运行migu_login.php）', 'debug' => $this->debugLogs];
        }

        if ($quality === 'flac24bit') {
            $audioType = $data['data']['audioFormatType'] ?? '';
            $apiUrl = $data['data']['url'] ?? '';
            if ($audioType !== 'ZQ' && strpos($apiUrl, '/flac/') !== false) {
                $candidate = str_replace('/flac/', '/flac_24bit/', $apiUrl);
                $ch = curl_init($candidate);
                curl_setopt_array($ch, [CURLOPT_NOBODY => true, CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 10, CURLOPT_SSL_VERIFYPEER => false, CURLOPT_SSL_VERIFYHOST => false]);
                curl_exec($ch);
                $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
                curl_close($ch);
                if ($httpCode === 200) { $data['data']['url'] = $candidate; $data['data']['audioFormatType'] = 'ZQ'; }
            }
        }

        $this->addLog("OK | url=" . substr($data['data']['url'], 0, 60));
        if ($lrc && preg_match('/^https?:\/\//', $lrc)) {
            $lrc = $this->fetchLrcContent($lrc);
        }
        return [
            'code' => 200,
            'url' => $data['data']['url'] ?? null,
            'lrc' => $lrc, 'picture' => $picture,
            'name' => $finalName, 'singer' => $finalSinger, 'songmid' => $songmid,
        ];
    }
}

// ============================================================
// 主路由
// ============================================================
$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);
$action = basename($path);
if (!in_array($action, ['url', 'info', 'lyric', 'health'])) {
    $action = $_GET['action'] ?? 'url';
}

// 健康检查
if ($action === 'health') {
    header('Content-Type: application/json; charset=utf-8');
    $checks = [];
    $checks['php_curl'] = function_exists('curl_init') ? 'OK' : 'FAIL';
    $checks['php_openssl'] = function_exists('openssl_public_encrypt') ? 'OK' : 'FAIL';
    $checks['python'] = is_executable($PYTHON) ? 'OK' : 'FAIL (' . $PYTHON . ')';
    // Python常驻服务状态（走socket /health，避免exec开销）
    if (file_exists(PY_SOCKET)) {
        $ch = curl_init('http://localhost/health');
        curl_setopt_array($ch, [
            CURLOPT_UNIX_SOCKET_PATH => PY_SOCKET,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => 3,
            CURLOPT_CONNECTTIMEOUT => 1,
        ]);
        $pyHealthBody = curl_exec($ch);
        $pyHealthCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
        $pyHealthData = json_decode($pyHealthBody, true);
        if ($pyHealthCode === 200 && $pyHealthData && ($pyHealthData['status'] ?? '') === 'ok') {
            $checks['python_service'] = 'OK (socket, py' . ($pyHealthData['python'] ?? '?') . ')';
            $checks['python_deps'] = 'OK (via socket)';
        } else {
            $checks['python_service'] = 'WARN (socket存在但无响应)';
            $checks['python_deps'] = 'UNKNOWN';
        }
    } else {
        $checks['python_service'] = '未运行 (使用exec模式)';
        // socket不可用时才exec检测依赖
        $pyCheck = [];
        exec($PYTHON . ' -c "import aiohttp,orjson,loguru,Crypto,cryptography,pydantic,requests,fastapi,uvicorn;print(\"OK\")" 2>&1', $pyCheck, $pyRet);
        $checks['python_deps'] = $pyRet === 0 ? 'OK' : 'FAIL (' . implode(' ', $pyCheck) . ')';
    }
    $checks['config'] = file_exists($CONFIG_FILE) ? 'OK' : 'FAIL';
    $checks['cache_writable'] = is_writable($CACHE_DIR) ? 'OK' : 'FAIL';
    $checks['logs_writable'] = is_writable($LOG_DIR) ? 'OK' : 'FAIL';
    $checks['data_writable'] = is_writable($DATA_DIR) ? 'OK' : 'FAIL';
    // 检查各平台配置
    $checks['kg_configured'] = !empty($CONFIG['modules']['platform']['kg']['users'][0]['token']) ? 'OK' : '未配置';
    $checks['tx_configured'] = !empty($CONFIG['modules']['platform']['tx']['users'][0]['token']) ? 'OK' : '未配置';
    $checks['wy_configured'] = !empty($CONFIG['wy']['cookie']) ? 'OK' : '未配置';
    $checks['migu_configured'] = !empty($CONFIG['migu']['accounts'][0]) && !empty($CONFIG['migu']['password']) ? 'OK' : '未配置';
    // 检查咪咕凭证
    $miguAccount = $CONFIG['migu']['accounts'][0] ?? '';
    $miguTokenFile = $DATA_DIR . '/cookies_' . $miguAccount . '.json';
    $checks['migu_credential'] = ($miguAccount && file_exists($miguTokenFile)) ? 'OK' : '未生成（请运行migu_login.php）';
    $allOk = !in_array('FAIL', array_map(function($v) { return explode(' ', $v)[0]; }, $checks));
    echo json_encode(['code' => $allOk ? 200 : 500, 'checks' => $checks], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
    exit;
}

$source = $_GET['source'] ?? '';
$songId = $_GET['songId'] ?? $_GET['id'] ?? '';
$songmid = $_GET['songmid'] ?? '';
$quality = $_GET['quality'] ?? '320k';
$name = $_GET['name'] ?? '';
$singer = $_GET['singer'] ?? '';
// nocache=1 跳过缓存（用于测试）
$nocache = isset($_GET['nocache']) && in_array(strtolower($_GET['nocache']), ['1', 'true', 'yes'], true);

if (!$source) {
    echo json_encode(['code' => 400, 'message' => '缺少 source 参数'], JSON_UNESCAPED_UNICODE);
    exit;
}

$supported = ['kg', 'tx', 'wy', 'kw', 'migu'];
if (!in_array($source, $supported)) {
    echo json_encode(['code' => 400, 'message' => "不支持的平台: $source，支持: " . implode('/', $supported)], JSON_UNESCAPED_UNICODE);
    exit;
}

app_log('REQUEST', "source=$source action=$action songId=$songId songmid=$songmid quality=$quality nocache=" . ($nocache ? '1' : '0'));

// 缓存键：lyric 类带 v2 前缀，避免旧缓存里未转换的 QRC/JSON 数据在升级后继续命中
// url 类缓存键用"实际音质"作为后缀，避免降级结果污染高音质请求
$cacheId = $source === 'migu' ? ($songmid ?: $songId ?: $name . '_' . $singer) : $songId;
$cachePrefix = ($action === 'lyric') ? 'lyrv2|' : '';
$cacheKey = $cachePrefix . "$source|$action|$cacheId|$quality";
if (!$nocache) {
    $cached = get_cache($cacheKey, $action === 'url' ? 1800 : 86400);
    if ($cached) {
        app_log('CACHE', "HIT $cacheKey");
        echo json_encode($cached, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        exit;
    }
} else {
    app_log('CACHE', "BYPASS (nocache=1) $cacheKey");
}

$result = null;

switch ($source) {
    case 'kg':
    case 'tx':
        $result = run_python('kg_tx.py', [$source, $action, $songId, $quality]);
        // 风控：不重试，直接返回指引
        if (is_risk_control($result)) {
            app_log('RISK', "$source 触发风控/人机验证，跳过重试");
            $result = ['code' => 403, 'message' => risk_message($source)];
        }
        // 普通失败：自动刷新token后重试一次
        elseif (($result['code'] ?? 500) !== 200 && $action !== 'refresh') {
            app_log('RETRY', "$source 第一次失败(code=" . ($result['code'] ?? '?') . ")，刷新token后重试");
            $refreshResult = run_python('kg_tx.py', [$source, 'refresh']);
            if (is_risk_control($refreshResult)) {
                app_log('RISK', "$source 刷新时触发风控");
                $result = ['code' => 403, 'message' => risk_message($source)];
            } else {
                $retryResult = run_python('kg_tx.py', [$source, $action, $songId, $quality]);
                if (is_risk_control($retryResult)) {
                    app_log('RISK', "$source 重试触发风控");
                    $result = ['code' => 403, 'message' => risk_message($source)];
                } elseif (($retryResult['code'] ?? 500) === 200) {
                    app_log('RETRY', "$source 重试成功");
                    $result = $retryResult;
                } else {
                    app_log('RETRY', "$source 重试仍失败");
                }
            }
        }
        // 透传音质诊断字段
        if ($action === 'url' && ($result['code'] ?? 500) === 200) {
            foreach (['quality','requested','fallback','upgraded','direction',
                      'fallback_chain','sent_level','returned_level','url_feature'] as $f) {
                if (isset($result[$f])) {
                    $result[$f] = $result[$f];
                }
            }
            app_log('QUALITY', "$source songId=$songId requested=" . ($result['requested'] ?? $quality)
                . " actual=" . ($result['quality'] ?? '?')
                . " direction=" . ($result['direction'] ?? '?')
                . " chain=" . ($result['fallback_chain'] ?? '-'));
        }
        break;

    case 'wy':
        $pyResult = run_python('wy.py', [$action, $songId, $quality]);
        if ($action === 'url') {
            if (isset($pyResult['playback']['url'])) {
                $pb = $pyResult['playback'];
                $result = [
                    'code' => 200, 'message' => '成功',
                    'url' => $pb['url'],
                    'quality' => $pb['quality'] ?? $quality,
                    'requested' => $pb['requested'] ?? $quality,
                    'fallback' => $pb['fallback'] ?? false,
                    'upgraded' => $pb['upgraded'] ?? false,
                    'direction' => $pb['direction'] ?? 'match',
                    'fallback_chain' => $pb['fallback_chain'] ?? '',
                    'sent_level' => $pb['sent_level'] ?? '',
                    'returned_level' => $pb['returned_level'] ?? '',
                    'url_feature' => $pb['url_feature'] ?? '',
                ];
                app_log('QUALITY', "wy songId=$songId requested=" . ($result['requested'])
                    . " actual=" . ($result['quality'])
                    . " direction=" . ($result['direction'])
                    . " chain=" . ($result['fallback_chain']));
            } else {
                $result = ['code' => 500, 'message' => $pyResult['playback']['error'] ?? '获取失败'];
            }
        } elseif ($action === 'info') {
            if (isset($pyResult['detail']['song'])) {
                $d = $pyResult['detail'];
                $result = ['code' => 200, 'message' => '成功', 'data' => [
                    'songId' => $d['song']['id'], 'songName' => $d['song']['name'],
                    'artistName' => $d['artist'], 'albumName' => $d['album']['name'],
                    'albumId' => $d['album']['id'], 'duration' => $d['song']['duration_text'],
                    'coverUrl' => $d['cover_sizes']['500'] ?? ($d['covers'][0] ?? ''),
                ]];
            } else {
                $result = ['code' => 500, 'message' => '获取歌曲信息失败'];
            }
        } elseif ($action === 'lyric') {
            if (isset($pyResult['lyric']['original'])) {
                $result = ['code' => 200, 'message' => '成功', 'data' => [
                    'lyric' => $pyResult['lyric']['original'],
                    'trans' => $pyResult['lyric']['translated'] ?? '',
                ]];
            } else {
                $result = ['code' => 500, 'message' => '获取歌词失败'];
            }
        }
        break;

    case 'kw':
        if ($action === 'info' || $action === 'lyric') {
            $kwInfo = kw_get_info($songId);
            if ($action === 'info') {
                if ($kwInfo && !empty($kwInfo['songName'])) {
                    $result = ['code' => 200, 'message' => '成功', 'data' => [
                        'songId' => $songId,
                        'songName' => $kwInfo['songName'] ?? '',
                        'artistName' => $kwInfo['artist'] ?? '',
                        'albumName' => $kwInfo['album'] ?? '',
                        'coverUrl' => $kwInfo['pic'] ?? '',
                    ]];
                } else {
                    $result = ['code' => 500, 'message' => '获取歌曲信息失败'];
                }
            } else {
                if ($kwInfo && !empty($kwInfo['lrc'])) {
                    $result = ['code' => 200, 'message' => '成功', 'data' => ['lyric' => $kwInfo['lrc'], 'trans' => '']];
                } else {
                    $result = ['code' => 500, 'message' => '获取歌词失败'];
                }
            }
        } else {
            $pyResult = run_python('kw_vip.py', [$songId, $quality, '--json']);
            if (isset($pyResult['url'])) {
                $result = [
                    'code' => 200, 'message' => '成功',
                    'url' => $pyResult['url'],
                    'quality' => $pyResult['quality'] ?? $quality,
                    'requested' => $pyResult['requested'] ?? $quality,
                    'fallback' => $pyResult['fallback'] ?? false,
                    'upgraded' => $pyResult['upgraded'] ?? false,
                    'direction' => $pyResult['direction'] ?? 'match',
                    'format' => $pyResult['format'] ?? '',
                    'br' => $pyResult['br'] ?? null,
                    'content_length' => $pyResult['content_length'] ?? null,
                    'estimated_kbps_by_head' => $pyResult['estimated_kbps_by_head'] ?? null,
                ];
                app_log('QUALITY', "kw songId=$songId requested=" . ($result['requested'])
                    . " actual=" . ($result['quality'])
                    . " direction=" . ($result['direction'])
                    . " cl=" . ($result['content_length'] ?? '?'));
            } else {
                $err = $pyResult['error'] ?? '获取失败';
                if (in_array($quality, ['flac', 'hires', 'master', 'atmos'])) {
                    $err = '该歌曲无损音质需要酷我VIP账号或解密代理（' . $err . '）';
                }
                $result = ['code' => 500, 'message' => $err];
            }
        }
        break;

    case 'migu':
        $migu = new MiguAPI($CONFIG);
        $migu->autoLogin();
        $miguResult = $migu->handle([
            'name' => $name,
            'singer' => $singer,
            'quality' => $quality,
            'songmid' => $songmid ?: $songId,
            'action' => $action,
        ]);
        if ($action === 'url') {
            if (isset($miguResult['url'])) {
                // 从 URL 推断实际音质
                $actualQ = migu_parse_quality_from_url($miguResult['url']);
                if (!$actualQ) $actualQ = $quality;
                $direction = migu_compare_quality($quality, $actualQ);
                $result = [
                    'code' => 200, 'message' => '成功',
                    'url' => $miguResult['url'],
                    'quality' => $actualQ,
                    'requested' => $quality,
                    'fallback' => ($actualQ !== $quality),
                    'upgraded' => ($direction === 'upgraded'),
                    'direction' => $direction,
                    'sent_level' => $quality,
                    'returned_level' => $actualQ,
                    'url_feature' => $actualQ,
                ];
                app_log('QUALITY', "migu songId=$songId requested=$quality actual=$actualQ direction=$direction");
            } else {
                $result = ['code' => $miguResult['code'] ?? 500, 'message' => $miguResult['msg'] ?? '获取失败'];
                if (isset($miguResult['debug']) && empty($SEC['hide_errors'])) $result['debug'] = $miguResult['debug'];
            }
        } elseif ($action === 'info') {
            if (isset($miguResult['name'])) {
                $result = ['code' => 200, 'message' => '成功', 'data' => [
                    'songId' => $miguResult['songmid'] ?? '', 'songName' => $miguResult['name'],
                    'artistName' => $miguResult['singer'], 'albumName' => '',
                    'coverUrl' => $miguResult['picture'] ?? '',
                ]];
            } else {
                $result = ['code' => 500, 'message' => $miguResult['msg'] ?? '获取失败'];
            }
        } elseif ($action === 'lyric') {
            if (isset($miguResult['lrc'])) {
                $result = ['code' => 200, 'message' => '成功', 'data' => ['lyric' => $miguResult['lrc'], 'trans' => '']];
            } else {
                $result = ['code' => 500, 'message' => $miguResult['msg'] ?? '获取失败'];
            }
        }
        break;
}

if ($result === null) {
    $result = ['code' => 400, 'message' => "不支持的操作: $action"];
}

// 缓存策略：
// - url 类：如果发生了降级，缓存键改用"实际音质"，避免下次请求 quality=high 时命中降级缓存
// - 非降级：按原 cacheKey 缓存
if (isset($result['code']) && $result['code'] === 200 && !$nocache) {
    if ($action === 'url' && isset($result['quality']) && $result['quality'] !== $quality) {
        // 降级了：用实际音质做键
        $actualCacheKey = $cachePrefix . "$source|$action|$cacheId|" . $result['quality'];
        set_cache($actualCacheKey, $result);
        // 不写"请求音质"键，避免污染
    } else {
        set_cache($cacheKey, $result);
    }
}

app_log('RESPONSE', "source=$source action=$action code=" . ($result['code'] ?? 'N/A')
    . (isset($result['quality']) ? " quality=" . $result['quality'] : '')
    . (isset($result['direction']) ? " direction=" . $result['direction'] : ''));
echo json_encode($result, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
