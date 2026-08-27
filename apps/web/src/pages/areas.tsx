import { Link } from "@tanstack/react-router";
import { useSession } from "../api/queries";
import { EmptyState, Page } from "./EmptyState";

export function HomePage() {
  const { data: session } = useSession();
  const ws = session?.workspaces.find((w) => w.id === session.current_workspace_id);
  return (
    <Page title={ws ? `Good to see you, ${session?.account.display_name}.` : "Home"} lead={ws ? `You're working in ${ws.name}.` : undefined}>
      <EmptyState
        title="Nothing scheduled yet"
        body="When you create content, drafts, approvals and upcoming posts show up here so you can see the week at a glance."
        action={
          <Link to="/create" className="cf-button cf-button--primary cf-button--md">
            Create something
          </Link>
        }
      />
    </Page>
  );
}

export function CreatePage() {
  return (
    <Page title="Create" lead="Start a brief and the factory takes it from there.">
      <EmptyState title="The Create flow lands in the next phase" body="You'll describe what you want in plain words, pick a persona and formats, and review a plan before anything is made." />
    </Page>
  );
}

export function InboxPage() {
  return (
    <Page title="Inbox" lead="Approvals, questions from the factory, and replies that need you.">
      <EmptyState title="Your inbox is clear" body="Items appear here when a pipeline pauses for your decision or a reply needs a human." />
    </Page>
  );
}

export function PersonasPage() {
  return (
    <Page title="Personas" lead="The voices your content is written in.">
      <EmptyState title="No personas yet" body="A persona holds tone, boundaries and disclosure rules. Every piece of content is made through one." />
    </Page>
  );
}

export function CalendarPage() {
  return (
    <Page title="Calendar" lead="What goes out, where, and when.">
      <EmptyState title="Nothing on the calendar" body="Scheduled and published posts will be laid out by day across all connected channels." />
    </Page>
  );
}

export function ProjectsPage() {
  return (
    <Page title="Projects" lead="Group related briefs, drafts and assets.">
      <EmptyState title="No projects yet" body="Projects keep a campaign's pieces together. The first one is created for you when you start creating." />
    </Page>
  );
}

export function AssetsPage() {
  return (
    <Page title="Assets" lead="Images, video, audio and documents the factory can use.">
      <EmptyState title="The library is empty" body="Uploads and generated media are stored with provenance so you always know where something came from." />
    </Page>
  );
}

export function BrandPage() {
  return (
    <Page title="Brand" lead="Colours, fonts, logos and rules for rendered content.">
      <EmptyState title="No brand kit yet" body="A brand kit makes every rendered image and video consistent. This is separate from the app theme." />
    </Page>
  );
}

export function SourcesPage() {
  return (
    <Page title="Sources" lead="Where research and evidence come from.">
      <EmptyState title="No sources connected" body="Add feeds, sites and documents; claims in your content are linked back to them." />
    </Page>
  );
}

export function TemplatesPage() {
  return (
    <Page title="Templates" lead="Reusable layouts for posts, threads, clips and newsletters.">
      <EmptyState title="No templates yet" body="Templates arrive with the rendering phase. You'll be able to preview and tweak each one." />
    </Page>
  );
}

export function ConnectionsPage() {
  return (
    <Page title="Connections" lead="Accounts and services the factory can publish to or read from.">
      <EmptyState title="Nothing connected" body="Connections are added one at a time and stay disabled until you turn distribution on." />
    </Page>
  );
}

export function AnalyticsPage() {
  return (
    <Page title="Analytics" lead="How published content performs.">
      <EmptyState title="No data yet" body="Once something is published, reach and engagement per channel show up here." />
    </Page>
  );
}

export function OperationsPage() {
  return (
    <Page title="Operations" lead="Owner tools: workers, queues, the kill switch and budgets.">
      <EmptyState title="All quiet" body="Health of the pipeline, running jobs and the global kill switch will be controlled from here." />
    </Page>
  );
}

export function NotFoundPage() {
  return (
    <Page title="Page not found">
      <EmptyState
        title="There's nothing at this address"
        body="The link may be old or mistyped."
        action={
          <Link to="/" className="cf-button cf-button--secondary cf-button--md">
            Go home
          </Link>
        }
      />
    </Page>
  );
}
