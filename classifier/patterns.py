"""
Failure classification patterns for root cause analysis.

Each category contains weighted keyword patterns used to score
incoming alerts and generate hypotheses.

Author: Jordan (DEV-1)
"""

from typing import Dict, List, Tuple
import re


# Pattern structure: (regex_pattern, weight, description)
PatternEntry = Tuple[str, float, str]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FAILURE CATEGORY PATTERNS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PATTERN_MAP: Dict[str, List[PatternEntry]] = {

    # ──────────────────────────────────────────────────────
    # DB CONNECTIVITY
    # ──────────────────────────────────────────────────────
    "db_connectivity": [
        (r"connection.{0,20}(refused|reset|closed|failed)", 10.0, "DB connection failure"),
        (r"could not connect to (database|postgres|mysql|mongodb)", 10.0, "Explicit DB connection error"),
        (r"(postgres|mysql|mongodb|redis).{0,30}(timeout|unreachable)", 9.0, "DB timeout"),
        (r"too many connections", 8.0, "Connection pool exhaustion"),
        (r"connection pool.{0,20}(exhausted|timeout)", 8.0, "Pool issues"),
        (r"database.{0,20}(unavailable|down|unreachable)", 9.0, "DB unavailable"),
        (r"psycopg2\.\w*Error", 7.0, "PostgreSQL driver error"),
        (r"pymongo\.errors", 7.0, "MongoDB driver error"),
        (r"sqlalchemy\.exc", 6.0, "SQLAlchemy exception"),
        (r"max_connections", 6.0, "Connection limit"),
        (r"idle.{0,10}transaction", 5.0, "Idle transaction"),
    ],

    # ──────────────────────────────────────────────────────
    # DNS FAILURE
    # ──────────────────────────────────────────────────────
    "dns_failure": [
        (r"(dns|name).{0,20}resolution failed", 10.0, "DNS resolution explicit failure"),
        (r"could not resolve.{0,30}(host|hostname|domain)", 10.0, "Hostname resolution failure"),
        (r"getaddrinfo failed", 9.0, "System DNS lookup failure"),
        (r"nodename nor servname provided", 8.0, "DNS name error"),
        (r"temporary failure in name resolution", 10.0, "DNS temporary failure"),
        (r"no such host", 9.0, "Host not found"),
        (r"nxdomain", 9.0, "Non-existent domain"),
        (r"dns.{0,20}(timeout|error)", 8.0, "DNS timeout"),
        (r"name or service not known", 9.0, "DNS service error"),
    ],

    # ──────────────────────────────────────────────────────
    # CERTIFICATE EXPIRY
    # ──────────────────────────────────────────────────────
    "certificate_expiry": [
        (r"certificate.{0,30}(expired|invalid|verify failed)", 10.0, "Certificate verification failure"),
        (r"ssl.{0,20}(certificate|cert).{0,20}(expired|invalid)", 10.0, "SSL cert expired"),
        (r"tls.{0,20}handshake.{0,20}failed", 9.0, "TLS handshake failure"),
        (r"certificate has expired", 10.0, "Explicit cert expiry"),
        (r"x509.{0,20}(expired|invalid)", 9.0, "X509 cert issue"),
        (r"ssl(error|exception)", 7.0, "SSL error"),
        (r"certificate_verify_failed", 10.0, "Cert verification failed"),
        (r"unable to get local issuer certificate", 8.0, "Cert chain issue"),
        (r"self.{0,5}signed certificate", 6.0, "Self-signed cert"),
    ],

    # ──────────────────────────────────────────────────────
    # NETWORK / INTRA-SERVICE
    # ──────────────────────────────────────────────────────
    "network_intra_service": [
        (r"connection.{0,20}timed out", 8.0, "Connection timeout"),
        (r"(read|write).{0,20}timeout", 8.0, "Read/write timeout"),
        (r"request.{0,20}timeout", 7.0, "Request timeout"),
        (r"(service|endpoint).{0,30}(unreachable|unavailable)", 9.0, "Service unreachable"),
        (r"connection refused", 7.0, "Connection refused"),
        (r"broken pipe", 7.0, "Broken pipe"),
        (r"network.{0,20}(unreachable|error)", 8.0, "Network error"),
        (r"503.{0,20}service unavailable", 8.0, "503 error"),
        (r"502.{0,20}bad gateway", 8.0, "502 error"),
        (r"504.{0,20}gateway timeout", 9.0, "504 error"),
        (r"no route to host", 8.0, "Routing failure"),
        (r"connection reset by peer", 7.0, "Connection reset"),
        (r"(upstream|downstream).{0,30}(timeout|failed|error)", 7.0, "Service dependency failure"),
    ],

    # ──────────────────────────────────────────────────────
    # CODE / LOGIC ERROR
    # ──────────────────────────────────────────────────────
    "code_logic_error": [
        (r"(nullpointerexception|npe)", 9.0, "Null pointer exception"),
        (r"attributeerror.{0,30}'nonetype'", 9.0, "Python None attribute access"),
        (r"keyerror", 8.0, "Missing key"),
        (r"indexerror", 8.0, "Index out of bounds"),
        (r"typeerror", 7.0, "Type mismatch"),
        (r"valueerror", 7.0, "Invalid value"),
        (r"assertion.{0,20}(failed|error)", 8.0, "Assertion failure"),
        (r"unhandled (exception|error)", 8.0, "Unhandled exception"),
        (r"division by zero", 9.0, "Math error"),
        (r"(stack overflow|recursion limit)", 8.0, "Stack/recursion issue"),
        (r"undefined (variable|method|function)", 8.0, "Undefined reference"),
        (r"cannot read property.{0,30}undefined", 8.0, "JS undefined property"),
        (r"unexpected.{0,20}(token|end of input)", 7.0, "Parse error"),
        (r"validation.{0,20}(failed|error)", 6.0, "Validation failure"),
    ],

    # ──────────────────────────────────────────────────────
    # CONFIG DRIFT
    # ──────────────────────────────────────────────────────
    "config_drift": [
        (r"(configuration|config).{0,20}(missing|not found|invalid)", 10.0, "Config missing/invalid"),
        (r"environment variable.{0,30}not (set|found)", 9.0, "Missing env var"),
        (r"(missing|invalid).{0,20}(api.{0,5}key|token|credential)", 9.0, "Missing credentials"),
        (r"permission.{0,20}denied", 7.0, "Permission issue"),
        (r"(unauthorized|401)", 7.0, "Auth failure"),
        (r"(forbidden|403)", 7.0, "Authorization failure"),
        (r"feature flag.{0,20}(not found|disabled)", 6.0, "Feature flag issue"),
        (r"(setting|property).{0,30}(undefined|null)", 6.0, "Undefined setting"),
        (r"file not found.{0,30}\.(yaml|yml|json|conf|properties)", 8.0, "Config file missing"),
    ],

    # ──────────────────────────────────────────────────────
    # DEPENDENCY FAILURE
    # ──────────────────────────────────────────────────────
    "dependency_failure": [
        (r"(kafka|rabbitmq|redis|elasticsearch).{0,30}(unavailable|down|timeout)", 9.0, "External dependency down"),
        (r"(third.{0,5}party|external).{0,30}(api|service).{0,30}(failed|timeout|error)", 8.0, "External API failure"),
        (r"import.{0,20}(error|failed)", 7.0, "Import/dependency error"),
        (r"module.{0,20}not found", 8.0, "Missing module"),
        (r"package.{0,20}not (found|installed)", 7.0, "Missing package"),
        (r"no module named", 8.0, "Python module missing"),
        (r"class.{0,20}not found", 7.0, "Missing class"),
        (r"(kafka|queue|stream).{0,30}(error|exception)", 7.0, "Queue/stream error"),
    ],

    # ──────────────────────────────────────────────────────
    # MEMORY / RESOURCE EXHAUSTION
    # ──────────────────────────────────────────────────────
    "memory_resource_exhaustion": [
        (r"out of memory", 10.0, "OOM explicit"),
        (r"(oom|outofmemory)", 10.0, "OOM abbreviated"),
        (r"memory.{0,20}(exhausted|limit|exceeded)", 9.0, "Memory limit"),
        (r"cannot allocate memory", 10.0, "Allocation failure"),
        (r"heap.{0,20}(space|exhausted|full)", 9.0, "Heap exhaustion"),
        (r"(disk|storage).{0,20}(full|exhausted|exceeded)", 9.0, "Disk full"),
        (r"no space left on device", 10.0, "No disk space"),
        (r"too many open files", 8.0, "File descriptor limit"),
        (r"resource.{0,20}(exhausted|limit|exceeded)", 8.0, "Resource limit"),
        (r"thread.{0,20}pool.{0,20}(exhausted|full)", 7.0, "Thread pool exhaustion"),
        (r"cpu.{0,20}(limit|throttl)", 7.0, "CPU throttling"),
    ],
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PATTERN COMPILATION (for performance)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMPILED_PATTERNS: Dict[str, List[Tuple[re.Pattern, float, str]]] = {
    category: [
        (re.compile(pattern, re.IGNORECASE), weight, description)
        for pattern, weight, description in patterns
    ]
    for category, patterns in PATTERN_MAP.items()
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIDENCE LEVEL THRESHOLDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CONFIDENCE_THRESHOLDS = {
    "Low": (0.0, 40.0),
    "Medium": (40.0, 70.0),
    "High": (70.0, 85.0),
    "Confirmed": (85.0, 100.0),
}


def get_confidence_level(percentage: float) -> str:
    """Convert percentage to confidence level enum value"""
    for level, (min_val, max_val) in CONFIDENCE_THRESHOLDS.items():
        if min_val <= percentage < max_val:
            return level
    return "Confirmed"  # >= 85%
