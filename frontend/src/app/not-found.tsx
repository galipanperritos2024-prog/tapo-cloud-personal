import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 text-center">
      <h1 className="text-2xl font-semibold">Página no encontrada</h1>
      <Link href="/" className="btn-primary">
        Volver al inicio
      </Link>
    </div>
  );
}
