import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { installJsdomShims } from "@content-factory/test-support";
import {afterAll, afterEach, beforeAll} from "vitest";
import { aiReviewMode, aiReviewPosts, aiReviewStore, appearancePuts, graphRunPosts, graphStore, hfTokenStore, installPosts, keymapPuts, relinkPosts, themePuts, uploadPosts, verdictPosts } from "./msw/handlers";
import { server } from "./msw/server";

installJsdomShims();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  themePuts.length = 0;
  keymapPuts.length = 0;
  appearancePuts.length = 0;
  graphStore.clear();
  graphRunPosts.length = 0;
  installPosts.length = 0;
  relinkPosts.length = 0;
  uploadPosts.length = 0;
  verdictPosts.length = 0;
  aiReviewPosts.length = 0;
  aiReviewMode.next = "ok";
  aiReviewStore.clear();
  hfTokenStore.token = null;
  window.localStorage.clear();
  document.documentElement.removeAttribute("style");
  // PrefsProvider paints these onto <html>; they would otherwise leak into the next test.
  document.documentElement.removeAttribute("data-density");
  document.documentElement.removeAttribute("data-motion");
});
afterAll(() => server.close());
