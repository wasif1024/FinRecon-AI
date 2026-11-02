import Link from "next/link";

export default function CaseNotFound() {
  return (
    <div className="space-y-2 py-12 text-center">
      <h1 className="text-xl font-semibold">Case not found</h1>
      <p className="text-sm text-muted-foreground">It doesn&apos;t exist, or it belongs to another organisation.</p>
      <Link href="/cases" className="text-sm underline underline-offset-4">Back to cases</Link>
    </div>
  );
}
