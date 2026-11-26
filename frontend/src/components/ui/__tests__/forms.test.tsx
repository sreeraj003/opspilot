import { zodResolver } from "@hookform/resolvers/zod";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useForm } from "react-hook-form";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { Button, ButtonLink } from "@/components/ui/Button";
import { buttonClasses } from "@/components/ui/buttonStyles";
import { Checkbox } from "@/components/ui/Checkbox";
import { PasswordField, TextField } from "@/components/ui/Field";
import { FormField } from "@/components/ui/FormField";
import { SearchInput, Select, Textarea } from "@/components/ui/Input";
import { Switch } from "@/components/ui/Switch";

describe("Button", () => {
  it.each([
    ["primary", "bg-accent"],
    ["secondary", "border-line"],
    ["ghost", "hover:bg-surface-muted"],
    ["danger", "bg-danger"],
    ["danger-ghost", "text-danger-text"],
  ] as const)("applies the %s variant from the design tokens", (variant, expected) => {
    render(<Button variant={variant}>Go</Button>);
    expect(screen.getByRole("button", { name: "Go" }).className).toContain(expected);
  });

  it.each([
    ["sm", "h-8"],
    ["md", "h-9"],
    ["lg", "h-10"],
    ["icon", "w-9"],
  ] as const)("applies the %s size", (size, expected) => {
    expect(buttonClasses({ size })).toContain(expected);
  });

  it("defaults to type=button so it never submits a form by accident", () => {
    render(<Button>Plain</Button>);
    expect(screen.getByRole("button")).toHaveAttribute("type", "button");
  });

  it("is disabled and busy while loading, and ignores clicks", async () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Save
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    await userEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("renders a router link styled as a button", () => {
    render(
      <MemoryRouter>
        <ButtonLink to="/somewhere" variant="primary">
          Open
        </ButtonLink>
      </MemoryRouter>,
    );
    const link = screen.getByRole("link", { name: "Open" });
    expect(link).toHaveAttribute("href", "/somewhere");
    expect(link.className).toContain("bg-accent");
  });
});

describe("FormField and controls", () => {
  it("associates the label, description and error with the control", () => {
    render(
      <FormField label="Name" description="As shown to colleagues." error="Enter a name." required>
        <Textarea />
      </FormField>,
    );
    const control = screen.getByLabelText("Name");
    expect(control.tagName).toBe("TEXTAREA");
    expect(control).toHaveAttribute("aria-invalid", "true");
    expect(control).toBeRequired();
    const error = screen.getByText("Enter a name.");
    expect(control.getAttribute("aria-describedby")).toContain(error.id);
    // The description is replaced by the error while there is one.
    expect(screen.queryByText("As shown to colleagues.")).not.toBeInTheDocument();
  });

  it("shows the description when there is no error", () => {
    render(
      <FormField label="Region" description="Where you work.">
        <Select>
          <option>North</option>
        </Select>
      </FormField>,
    );
    const select = screen.getByRole("combobox", { name: "Region" });
    expect(select).toHaveAccessibleDescription("Where you work.");
    expect(select).not.toHaveAttribute("aria-invalid");
  });

  it("toggles password visibility", async () => {
    const user = userEvent.setup();
    render(<PasswordField label="Password" />);
    const input = screen.getByLabelText("Password");
    expect(input).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "Show password" }));
    expect(input).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Hide password" })).toHaveAttribute("aria-pressed", "true");
  });

  it("clears a search box", async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    render(<SearchInput label="Search users" value="ada" onValueChange={onValueChange} />);
    expect(screen.getByRole("searchbox", { name: "Search users" })).toHaveValue("ada");
    await user.click(screen.getByRole("button", { name: "Clear search" }));
    expect(onValueChange).toHaveBeenCalledWith("");
  });

  it("checkbox and switch expose their state", async () => {
    const user = userEvent.setup();
    const onCheckedChange = vi.fn();
    render(
      <>
        <Checkbox label="Active" description="Can sign in" defaultChecked />
        <Switch label="Compact" checked={false} onCheckedChange={onCheckedChange} />
      </>,
    );
    expect(screen.getByRole("checkbox", { name: "Active" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Active" })).toHaveAccessibleDescription("Can sign in");
    const toggle = screen.getByRole("switch", { name: "Compact" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    await user.click(toggle);
    expect(onCheckedChange).toHaveBeenCalledWith(true);
  });
});

const schema = z.object({ email: z.email("Enter a valid email address."), name: z.string().min(1, "Enter a name.") });

function ExampleForm({ onSubmit }: { onSubmit: (values: z.infer<typeof schema>) => Promise<void> }) {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema), defaultValues: { email: "", name: "" } });
  return (
    <form noValidate onSubmit={handleSubmit(onSubmit)}>
      <TextField label="Name" error={errors.name?.message} {...register("name")} />
      <TextField label="Email" error={errors.email?.message} {...register("email")} />
      <Button type="submit" loading={isSubmitting}>
        Save
      </Button>
    </form>
  );
}

describe("form handling (react-hook-form + zod)", () => {
  it("shows field-level errors and does not submit invalid values", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async () => undefined);
    render(<ExampleForm onSubmit={onSubmit} />);

    await user.type(screen.getByLabelText("Email"), "nope");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Enter a name.")).toBeInTheDocument();
    expect(screen.getByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveAttribute("aria-invalid", "true");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("disables submission while the request runs", async () => {
    const user = userEvent.setup();
    let finish: () => void = () => undefined;
    const onSubmit = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
    render(<ExampleForm onSubmit={onSubmit} />);

    await user.type(screen.getByLabelText("Name"), "Ada");
    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("button", { name: "Save" })).toBeDisabled();
    finish();
    await screen.findByRole("button", { name: "Save" });
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });
});
