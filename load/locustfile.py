"""Simulated customers for `make load` (see load/run.py).

Each task is one whole chat, typed at human speed: verify by email, then ask about an order,
cancel a pending order, or return a delivered one, then say bye. Customers and orders come from
τ²-bench's data. Requests use `?wait=true`, so a request's time is the agent's reply time.

A chat refused because the app is full is counted as "POST /api/chats [refused: at capacity]"
(not a failure: that is backpressure doing its job).
"""

import json
import random
from pathlib import Path

import gevent
from locust import HttpUser, between, task

DB = json.loads((Path(__file__).resolve().parents[1] / "data" / "db.json").read_text())
CUSTOMERS = [
    {
        "email": user["email"],
        "pending": [o for o in user["orders"] if DB["orders"][o]["status"] == "pending"],
        "delivered": [o for o in user["orders"] if DB["orders"][o]["status"] == "delivered"],
    }
    for user in DB["users"].values()
]
MESSAGES = "/api/chats/[id]/messages"
CONFIRM = "/api/chats/[id]/confirm"


class Chat:
    def __init__(self, user: "Customer", view: dict) -> None:
        self.user, self.view = user, view

    @property
    def pending(self) -> dict | None:
        return self.view.get("pending")

    def _post(self, path: str, name: str, body: dict) -> bool:
        with self.user.client.post(
            f"/api/chats/{self.view['chat_id']}/{path}?wait=true",
            json=body,
            name=name,
            catch_response=True,
        ) as r:
            if r.status_code == 503 and r.json()["error"]["code"] == "at_capacity":
                r.success()
                r.request_meta["name"] = f"{name} [refused: at capacity]"
                return False
            if r.status_code != 202:
                r.failure(f"{r.status_code}: {r.text[:200]}")
                return False
            self.view = r.json()
            return True

    def say(self, text: str) -> bool:
        gevent.sleep(random.uniform(*self.user.typing_s))  # the customer reads and types
        if not self.pending or self.pending["type"] != "await_customer":
            return False
        ok = self._post(
            "messages", MESSAGES, {"interrupt_id": self.pending["interrupt_id"], "text": text}
        )
        while ok and self.pending and self.pending["type"] == "confirm":
            gevent.sleep(random.uniform(*self.user.typing_s))
            ok = self._post(
                "confirm",
                CONFIRM,
                {"interrupt_id": self.pending["interrupt_id"], "confirmed": True},
            )
        return ok


class Customer(HttpUser):
    wait_time = between(2, 5)  # between chats
    typing_s = (1.0, 3.0)

    def on_start(self) -> None:
        self.customer = random.choice(CUSTOMERS)

    def start_chat(self) -> Chat | None:
        with self.client.post("/api/chats", name="POST /api/chats", catch_response=True) as r:
            if r.status_code == 503:
                r.success()
                r.request_meta["name"] = "POST /api/chats [refused: at capacity]"
                return None
            if r.status_code != 201:
                r.failure(f"{r.status_code}: {r.text[:200]}")
                return None
            return Chat(self, r.json())

    def verified_chat(self) -> Chat | None:
        chat = self.start_chat()
        if chat and chat.say(f"Hi, my email is {self.customer['email']}"):
            return chat
        return None

    @task(3)
    def ask_about_an_order(self) -> None:
        orders = self.customer["pending"] + self.customer["delivered"]
        if not orders or not (chat := self.verified_chat()):
            return
        if chat.say(f"What's the status of order {random.choice(orders)}?"):
            chat.say("bye")

    @task(2)
    def cancel_an_order(self) -> None:
        if not self.customer["pending"] or not (chat := self.verified_chat()):
            return
        order = random.choice(self.customer["pending"])
        if chat.say(f"Please cancel order {order}, I ordered it by mistake"):
            chat.say("bye")

    @task(1)
    def return_an_order(self) -> None:
        if not self.customer["delivered"] or not (chat := self.verified_chat()):
            return
        order = random.choice(self.customer["delivered"])
        if chat.say(f"I want to return everything in {order}"):
            # If asked where the refund should go, take the first option.
            if chat.pending and chat.pending["type"] == "await_customer" and not chat.view["ended"]:
                chat.say("1")
            chat.say("bye")
