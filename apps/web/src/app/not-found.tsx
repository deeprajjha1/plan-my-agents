import Link from "next/link";

export default function NotFound() {
  return (
    <div className="surface p-8">
      <h1 className="text-xl font-semibold text-ink-900">Not found</h1>
      <p className="mt-2 text-sm text-ink-600">
        The category or agent you tried to open is not in the discovery store
        right now.
      </p>
      <Link
        href="/categories"
        className="mt-4 inline-block text-sm text-accent-600 hover:underline"
      >
        Back to all categories
      </Link>
    </div>
  );
}
