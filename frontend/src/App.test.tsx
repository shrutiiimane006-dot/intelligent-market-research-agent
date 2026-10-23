import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

describe("authentication", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/auth/csrf")) return new Response(JSON.stringify({ csrf_token: "test-token" }), { status: 200 });
      if (url.endsWith("/api/auth/me")) return new Response(JSON.stringify({ error: { message: "Please log in." } }), { status: 401 });
      if (url.endsWith("/api/auth/login")) return new Response(JSON.stringify({ user: { id: 1, name: "Test User", email: "test@example.com" } }), { status: 200 });
      if (url.endsWith("/api/dashboard/summary")) return new Response(JSON.stringify({ summary: { total: 0, completed: 0, in_progress: 0, incomplete: 0 } }), { status: 200 });
      if (url.endsWith("/api/dashboard/recent")) return new Response(JSON.stringify({ items: [] }), { status: 200 });
      return new Response("{}", { status: 200 });
    }));
  });

  it("submits login credentials and opens the dashboard", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "Welcome back" });
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "test@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a-secure-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    await waitFor(() => expect(screen.getByText("Good research starts with a question.")).toBeInTheDocument());
  });

  it("registers with credentials and the refreshed CSRF token", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/auth/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "registration-token" }), { status: 200 });
      }
      if (url.endsWith("/api/auth/me")) {
        return new Response(JSON.stringify({ error: { message: "Please log in." } }), { status: 401 });
      }
      if (url.endsWith("/api/auth/register")) {
        return new Response(JSON.stringify({
          user: { id: 1, name: "New Researcher", email: "new@example.com" },
        }), { status: 201 });
      }
      if (url.endsWith("/api/dashboard/summary")) {
        return new Response(JSON.stringify({
          summary: { total: 0, completed: 0, in_progress: 0, incomplete: 0 },
        }), { status: 200 });
      }
      if (url.endsWith("/api/dashboard/recent")) {
        return new Response(JSON.stringify({ items: [] }), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });

    render(<App />);
    await screen.findByRole("heading", { name: "Welcome back" });
    fireEvent.click(screen.getByRole("button", { name: /Create an account/ }));
    fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "New Researcher" } });
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "new@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a-secure-password-123" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "a-secure-password-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    await screen.findByText("Good research starts with a question.");
    const csrfRequest = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/api/auth/csrf"));
    const registrationRequest = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/api/auth/register"));
    expect(csrfRequest?.[1]?.credentials).toBe("include");
    expect(registrationRequest?.[1]?.credentials).toBe("include");
    expect(new Headers(registrationRequest?.[1]?.headers).get("X-CSRF-Token")).toBe("registration-token");
    expect(JSON.parse(String(registrationRequest?.[1]?.body))).toEqual({
      name: "New Researcher",
      email: "new@example.com",
      password: "a-secure-password-123",
      confirm_password: "a-secure-password-123",
    });
  });

  it("submits a research question and displays the persisted job status", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/auth/csrf")) return new Response(JSON.stringify({ csrf_token: "test-token" }), { status: 200 });
      if (url.endsWith("/api/auth/me")) return new Response(JSON.stringify({ error: { message: "Please log in." } }), { status: 401 });
      if (url.endsWith("/api/auth/login")) return new Response(JSON.stringify({ user: { id: 1, name: "Test User", email: "test@example.com" } }), { status: 200 });
      if (url.endsWith("/api/dashboard/summary")) return new Response(JSON.stringify({ summary: { total: 0, completed: 0, in_progress: 0, incomplete: 0 } }), { status: 200 });
      if (url.endsWith("/api/dashboard/recent")) return new Response(JSON.stringify({ items: [] }), { status: 200 });
      if (url.endsWith("/api/research")) return new Response(JSON.stringify({
        job: {
          id: "job-1",
          status: "pending",
          original_query: "EV charging market trends",
          created_at: "2026-10-21T00:00:00Z",
          stages: [{ name: "planning", status: "pending" }],
        },
      }), { status: 202 });
      return new Response("{}", { status: 200 });
    });

    render(<App />);
    await screen.findByRole("heading", { name: "Welcome back" });
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "test@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a-secure-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    await screen.findByText("Good research starts with a question.");

    fireEvent.click(screen.getByRole("button", { name: "New research" }));
    fireEvent.change(screen.getByLabelText(/Research question/), { target: { value: "EV charging market trends" } });
    fireEvent.click(screen.getByRole("button", { name: /Start research/ }));

    expect(await screen.findByText("EV charging market trends")).toBeInTheDocument();
    expect(screen.getByText("pending", { selector: ".status" })).toBeInTheDocument();
    const request = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/api/research"));
    expect(request).toBeDefined();
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({ question: "EV charging market trends", scope: {} });
  });
});
