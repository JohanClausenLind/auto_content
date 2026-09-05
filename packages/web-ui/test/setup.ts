import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { installJsdomShims } from "@content-factory/test-support";
import {afterEach} from "vitest";

installJsdomShims();

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  document.documentElement.removeAttribute("style");
});
