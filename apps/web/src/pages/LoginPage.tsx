import { Button, TextField } from "@content-factory/web-ui";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { browserSupportsWebAuthn, startAuthentication, type PublicKeyCredentialRequestOptionsJSON } from "@simplewebauthn/browser";
import { useState, type FormEvent } from "react";
import { api, isApiError } from "../api/client";
import { setSession } from "../api/queries";
import type { Session } from "../api/types";

type Step = { kind: "password" } | { kind: "totp"; methods: ("totp" | "passkey")[] };

export function LoginPage() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>({ kind: "password" });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<null | "password" | "totp" | "passkey">(null);
  const [error, setError] = useState<string | null>(null);

  const finish = (session: Session) => {
    setSession(client, session);
    void navigate({ to: "/" });
  };

  const fail = (e: unknown, fallback: string) => setError(isApiError(e) ? e.detail : e instanceof Error ? e.message : fallback);

  const submitPassword = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy("password");
    try {
      const result = await api.session.login(username.trim(), password);
      if (result.kind === "session") finish(result.session);
      else setStep({ kind: "totp", methods: result.methods });
    } catch (err) {
      fail(err, "Sign-in failed.");
    } finally {
      setBusy(null);
    }
  };

  const submitTotp = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy("totp");
    try {
      finish(await api.session.totp(code.replace(/\s+/g, "")));
    } catch (err) {
      fail(err, "That code didn't work.");
    } finally {
      setBusy(null);
    }
  };

  const usePasskey = async () => {
    setError(null);
    setBusy("passkey");
    try {
      const { options, challenge_id } = await api.session.passkeyOptions();
      const credential = await startAuthentication({ optionsJSON: options as PublicKeyCredentialRequestOptionsJSON });
      finish(await api.session.passkeyVerify(challenge_id, credential));
    } catch (err) {
      if (err instanceof Error && err.name === "NotAllowedError") setError("Passkey prompt was cancelled.");
      else fail(err, "Passkey sign-in failed.");
    } finally {
      setBusy(null);
    }
  };

  const passkeyAvailable = typeof window !== "undefined" && browserSupportsWebAuthn();

  return (
    <main className="cf-login">
      <section className="cf-login__card" aria-labelledby="cf-login-title">
        <header className="cf-login__header">
          <span className="cf-brand" aria-hidden="true">F</span>
          <h1 id="cf-login-title" className="cf-login__title">
            {step.kind === "password" ? "Sign in to Content Factory" : "One more step"}
          </h1>
          <p className="cf-login__lead">
            {step.kind === "password" ? "This is your private, self-hosted console." : "Enter the 6-digit code from your authenticator app."}
          </p>
        </header>

        {step.kind === "password" ? (
          <form className="cf-login__form" onSubmit={(e) => void submitPassword(e)} noValidate>
            <TextField label="Username" name="username" value={username} onChange={setUsername} autoComplete="username" isRequired autoFocus />
            <TextField label="Password" name="password" type="password" value={password} onChange={setPassword} autoComplete="current-password" isRequired />
            {error && (
              <p role="alert" className="cf-login__error">
                {error}
              </p>
            )}
            <Button type="submit" variant="primary" isDisabled={busy !== null || !username || !password} isPending={busy === "password"}>
              {busy === "password" ? "Signing in…" : "Sign in"}
            </Button>
            <div className="cf-login__divider" role="presentation">
              <span>or</span>
            </div>
            <Button variant="secondary" onPress={() => void usePasskey()} isDisabled={busy !== null || !passkeyAvailable} isPending={busy === "passkey"}>
              {busy === "passkey" ? "Waiting for your passkey…" : "Use a passkey"}
            </Button>
            {!passkeyAvailable && <p className="cf-login__hint">Passkeys aren't available in this browser.</p>}
          </form>
        ) : (
          <form className="cf-login__form" onSubmit={(e) => void submitTotp(e)} noValidate>
            <TextField
              label="Authenticator code"
              name="totp"
              value={code}
              onChange={setCode}
              inputMode="numeric"
              autoComplete="one-time-code"
              isRequired
              autoFocus
              description="Codes change every 30 seconds."
            />
            {error && (
              <p role="alert" className="cf-login__error">
                {error}
              </p>
            )}
            <Button type="submit" variant="primary" isDisabled={busy !== null || code.replace(/\s+/g, "").length < 6} isPending={busy === "totp"}>
              {busy === "totp" ? "Checking…" : "Continue"}
            </Button>
            <div className="cf-login__row">
              {step.methods.includes("passkey") && passkeyAvailable && (
                <Button variant="ghost" size="sm" onPress={() => void usePasskey()} isDisabled={busy !== null}>
                  Use a passkey instead
                </Button>
              )}
              <Button
                variant="ghost"
                size="sm"
                onPress={() => {
                  setStep({ kind: "password" });
                  setCode("");
                  setError(null);
                }}
              >
                Back
              </Button>
            </div>
          </form>
        )}
      </section>
    </main>
  );
}
