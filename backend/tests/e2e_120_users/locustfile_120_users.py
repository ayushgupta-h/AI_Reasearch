"""
backend/tests/e2e_120_users/locustfile_120_users.py
===================================================
Locust Concurrent Load Test for Research AI Quest with 120 Distinct Users.

Exercises realistic user flows with separate tags for fast API requests
vs long-running research/document operations:
- Authentication & Profile Checks
- Projects CRUD
- History & Session Retrieval
- Document Management & Chat
- Report Comparison
- System Monitoring & Health Probes

Stages:
  10 -> 30 -> 60 -> 90 -> 120 users
"""

import itertools
import random
import uuid
from locust import HttpUser, between, task, tag

# Pool of 120 distinct test identities
NUM_USERS = 120
USER_POOL = [
    {
        "user_id": f"locust-user-{i:03d}",
        "email": f"locust_analyst_{i:03d}@research-ai-quest.io",
        "name": f"Locust Analyst {i:03d}",
    }
    for i in range(1, NUM_USERS + 1)
]

_user_counter = itertools.cycle(USER_POOL)


class ResearchAIQuestUser(HttpUser):
    wait_time = between(1.0, 3.0)

    def on_start(self):
        """Assign a unique user identity to each virtual user instance."""
        self.user_data = next(_user_counter)
        self.user_id = self.user_data["user_id"]
        # Authenticated requests pass user context via headers or query params
        self.headers = {
            "X-User-Id": self.user_id,
            "X-User-Email": self.user_data["email"],
            "Authorization": f"Bearer mock-jwt-{self.user_id}",
        }
        self.project_ids = []
        self.session_ids = []
        self.document_ids = []
        self.comparison_ids = []

    @tag("auth", "api")
    @task(3)
    def check_auth_and_config(self):
        """Verify auth config and user profile status."""
        self.client.get("/auth/config", name="[Auth] Config")
        self.client.get(f"/auth/me?user_id={self.user_id}", headers=self.headers, name="[Auth] Me")

    @tag("projects", "api")
    @task(4)
    def project_operations(self):
        """Create and retrieve project workspaces."""
        proj_name = f"Project {uuid.uuid4().hex[:6]}"
        res = self.client.post(
            "/projects",
            json={"name": proj_name, "description": "Automated benchmark workspace", "color": "#3b82f6"},
            headers=self.headers,
            name="[Projects] Create",
        )
        if res.status_code in (200, 201):
            pdata = res.json()
            pid = pdata.get("id") or pdata.get("project_id")
            if pid:
                self.project_ids.append(pid)
                self.client.get(f"/projects/{pid}", headers=self.headers, name="[Projects] Get Details")

        self.client.get("/projects", headers=self.headers, name="[Projects] List")

    @tag("history", "api")
    @task(5)
    def history_operations(self):
        """Fetch and search research history."""
        self.client.get("/sessions", headers=self.headers, name="[History] List Sessions")
        if self.session_ids:
            sid = random.choice(self.session_ids)
            self.client.get(f"/sessions/{sid}", headers=self.headers, name="[History] Get Session Details")

    @tag("monitoring", "api")
    @task(4)
    def monitoring_health_checks(self):
        """Poll system observability, infrastructure probes, and provider status."""
        self.client.get("/monitoring/overview", headers=self.headers, name="[Monitoring] Overview")
        self.client.get("/monitoring/infrastructure", headers=self.headers, name="[Monitoring] Infrastructure")
        self.client.get("/monitoring/providers", headers=self.headers, name="[Monitoring] Providers")
        self.client.get("/monitoring/api-performance", headers=self.headers, name="[Monitoring] Performance")

    @tag("reviews", "api")
    @task(2)
    def review_center_checks(self):
        """Retrieve human review queue."""
        self.client.get("/reviews/pending", headers=self.headers, name="[Reviews] Pending List")

    @tag("documents", "document_ops")
    @task(2)
    def document_operations(self):
        """Upload a lightweight text/markdown research document and preview."""
        content = f"# Clinical Research Data\nAnalyst: {self.user_id}\nContent: Sample diagnostic metrics."
        res = self.client.post(
            "/documents",
            files={"file": (f"doc_{uuid.uuid4().hex[:6]}.txt", content.encode("utf-8"), "text/plain")},
            headers=self.headers,
            name="[Documents] Upload",
        )
        if res.status_code in (200, 201):
            doc_id = res.json().get("document_id") or res.json().get("id")
            if doc_id:
                self.document_ids.append(doc_id)
                self.client.get(f"/documents/{doc_id}/preview", headers=self.headers, name="[Documents] Preview")

    @tag("report_comparison", "comparison_ops")
    @task(2)
    def report_comparison_operations(self):
        """Trigger report comparison when documents or sessions exist."""
        if len(self.document_ids) >= 2:
            d1, d2 = random.sample(self.document_ids, 2)
            res = self.client.post(
                "/report-comparison/compare",
                json={"report_a": {"id": d1, "type": "uploaded"}, "report_b": {"id": d2, "type": "uploaded"}},
                headers=self.headers,
                name="[Comparison] Compare",
            )
            if res.status_code in (200, 201):
                cid = res.json().get("comparison_id")
                if cid:
                    self.comparison_ids.append(cid)
                    self.client.post(
                        "/report-comparison/chat",
                        json={"comparison_id": cid, "question": "Compare findings and identify disagreements."},
                        headers=self.headers,
                        name="[Comparison] Chat",
                    )
