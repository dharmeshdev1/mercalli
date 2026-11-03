import Link from "next/link";

export default function NotFound() {
  return (
    <div className="shell py-24">
      <h1 className="display text-3xl font-normal md:text-4xl">Nothing recorded here</h1>
      <p className="prose-measure mt-4 text-lg text-ink-soft">
        This page does not exist. The live record is on the <Link href="/">front page</Link>, and every watched service is listed under{" "}
        <Link href="/services/">Services</Link>.
      </p>
    </div>
  );
}
