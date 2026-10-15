import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterAll, describe, expect, it } from "vitest";

import { client } from "@/client/client.gen";
import { StatusPage } from "@/features/status/status-page";

const renderPage = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <StatusPage />
    </QueryClientProvider>,
  );

describe("StatusPage", () => {
  let server: Server;

  const listen = (status: number, body: unknown) =>
    new Promise<void>((resolve) => {
      server = createServer((_req, res) => {
        res.writeHead(status, { "content-type": "application/json" });
        res.end(JSON.stringify(body));
      }).listen(0, "127.0.0.1", () => {
        client.setConfig({ baseUrl: `http://127.0.0.1:${(server.address() as AddressInfo).port}` });
        resolve();
      });
    });

  afterAll(() => server?.close());

  it("shows the status returned by the generated SDK", async () => {
    await listen(200, { status: "ok" });
    renderPage();
    expect(await screen.findByText("ok")).toBeInTheDocument();
    expect(screen.getByText(/is up\./)).toBeInTheDocument();
    server.close();
  });

  it("explains what to do when the API is unreachable", async () => {
    await listen(503, { detail: "down" });
    renderPage();
    expect(await screen.findByText(/did not respond/)).toBeInTheDocument();
  });
});
