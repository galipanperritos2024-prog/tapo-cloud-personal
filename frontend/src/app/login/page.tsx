"use client";

import { Camera, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { ErrorBox, Spinner } from "@/components/ui";
import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [setup, setSetup] = useState<boolean | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ setup_required: boolean; authenticated: boolean }>("/api/auth/status")
      .then((s) => (s.authenticated ? router.replace("/") : setSetup(s.setup_required)))
      .catch(setError);
  }, [router]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (setup && password !== password2) return setError(new Error("Las contraseñas no coinciden"));
    setBusy(true);
    try {
      await api(setup ? "/api/auth/setup" : "/api/auth/login", { json: { username, password } });
      window.location.href = setup ? "/cameras/?add=1" : "/";
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-5 p-8">
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-accent">
            <Camera className="text-white" />
          </div>
          <div>
            <h1 className="text-xl font-semibold">Tapo Cloud Personal</h1>
            <p className="mt-1 text-sm text-muted">{setup ? "Crea la cuenta de administrador" : "Inicia sesión"}</p>
          </div>
        </div>
        {setup === null && !error ? (
          <div className="flex justify-center py-6 text-muted">
            <Spinner />
          </div>
        ) : (
          <>
            <label className="block">
              <span className="label">Usuario</span>
              <input className="input" autoComplete="username" required minLength={setup ? 3 : 1} value={username} onChange={(e) => setUsername(e.target.value)} />
            </label>
            <label className="block">
              <span className="label">Contraseña</span>
              <input className="input" type="password" autoComplete={setup ? "new-password" : "current-password"} required minLength={setup ? 10 : 1} value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            {setup && (
              <>
                <label className="block">
                  <span className="label">Repite la contraseña</span>
                  <input className="input" type="password" autoComplete="new-password" required value={password2} onChange={(e) => setPassword2(e.target.value)} />
                </label>
                <p className="flex gap-2 text-xs text-muted">
                  <ShieldCheck size={14} className="mt-0.5 shrink-0 text-ok" /> Mínimo 10 caracteres. Esta cuenta protege el acceso a tus cámaras desde cualquier dispositivo de tu red.
                </p>
              </>
            )}
            <ErrorBox error={error} />
            <button className="btn-primary w-full" disabled={busy}>
              {busy && <Spinner />} {setup ? "Crear cuenta y empezar" : "Entrar"}
            </button>
          </>
        )}
      </form>
    </div>
  );
}
