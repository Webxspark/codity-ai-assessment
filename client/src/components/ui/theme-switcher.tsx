import { Moon, Sun, SunMoon } from 'lucide-react';
import { useTheme } from '../theme-provider';
import { Button, cn } from '@heroui/react';

const ThemeSwitcher = ({ className }: { className?: string }) => {
    const { setTheme, theme } = useTheme();
    return (
        <div className={cn("absolute bottom-5 right-5", className)}>
            <Button
                variant="outline"
                className="rounded-full p-2"
                isIconOnly
                onClick={() => {
                    setTheme(theme == "dark" ? "light" : "dark");
                }}
            >
                {
                    theme == "dark" ? <Sun size={24} /> : (theme == 'light' ? <Moon size={24} /> : <SunMoon size={24} />)
                }
            </Button>
        </div>
    );
};

export default ThemeSwitcher;