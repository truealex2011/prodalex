from prometheus_client import Counter, Histogram

HTTP_LATENCY = Histogram("http_request_duration_seconds", "HTTP latency", ["endpoint", "status"])
FUNNEL = Counter("funnel_step_reached_total", "Product funnel", ["step"])
EMPTY_RESULTS = Counter("recommendations_empty_total", "Empty recommendation results")
FEEDBACK = Counter("feedback_total", "User reaction to a recommended car", ["liked"])
