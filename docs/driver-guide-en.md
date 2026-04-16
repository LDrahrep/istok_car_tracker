Drivers!
Everyone needs to update their data!
BOT -> @istok_cars_bot <- BOT
My Telegram -> @ldrahrep <- My Telegram
# Driver Guide
## Getting Started

1. Open the bot in Telegram
2. Type `/start` or press the **Start** button
3. Control buttons will appear at the bottom of the screen

## 🚗 Become a Driver

You only need to do this **once**. After registration you'll be able to add passengers.

1. Press **"🚗 Стать водителем"** (Become a driver)
2. Enter your **first and last name** exactly as written in the timesheet (Example: `Ivan Ivanov`)
3. Enter your **car model** (Example: `Toyota Camry`)
4. Enter your **license plate** (Example: `ABC 1234`)

✅ Done — you're registered as a driver.

### Possible errors

| Error | What to do |
|-------|-----------|
| "Сотрудника еще не добавили" (Employee not added yet) | Contact @ldrahrep — you're not in the employee list |
| "У тебя не указана смена" (Your shift is not specified) | Contact @ldrahrep — your shift is not set in the system |
| "Ты уже записан как пассажир" (You are already registered as a passenger) | You cannot be a driver — someone already listed you as their passenger. Ask that person to remove you |

---

## 👥 Add Passengers

Maximum **4 passengers**.

1. Press **"👥 Добавить пассажиров"** (Add passengers)
2. Type passenger names **each on a new line**:
   ```
   Ivan Ivanov
   Maria Ivanova
   Petr Petrov
   ```
3. The bot will verify each and add those it can find

### Rules

- ⚠️ **The passenger must be on the same shift as you**
- ⚠️ **A passenger cannot be assigned to two drivers at the same time**
  - If they're already riding with someone else — that driver has to remove them first
- ⚠️ **Write names exactly** — if you make a mistake, the bot will suggest similar options

### If the bot can't find a passenger

The bot will show a hint:
```
• Ivan: employee not added yet. Did you mean: Ivan Ivanov, Ivan Petrov
```
Rewrite the name correctly and send it again.

---

## 🧑‍🤝‍🧑 Remove a Passenger

If a passenger no longer rides with you — remove them so another driver can add them.

1. Press **"🧑‍🤝‍🧑 Удалить пассажира"** (Remove passenger)
2. You'll see buttons with your passengers' names
3. Tap the one you want to remove

---

## 📋 My Record

Check what's registered for you in the system.

Press **"📋 Моя запись"** (My record) — you'll see:
- Your name
- Car and license plate
- Passenger list

---

## 🛑 Stop Being a Driver

If you no longer drive anyone, or are leaving the company — remove yourself from the system.

1. Press **"🛑 Перестать быть водителем"** (Stop being a driver)
2. The bot will ask for confirmation: **✅ Да (Yes) / ❌ Нет (No)**
3. Press **✅ Да**

After this:
- Your record will be deleted from the driver list
- All your passengers will become "free"
- If you want to become a driver again — start with **"🚗 Стать водителем"**

---

## 📅 Passenger List Check

The bot may send you a message like this:
```
📅 Еженедельная проверка списка пассажиров

Текущие пассажиры:
Ivan Ivanov
Maria Ivanova

Всё актуально?
```

This happens in two cases:
- **Weekly check** — once a week to all drivers
- **Targeted check** — when @ldrahrep checks you individually

Review the list and answer:
- **✅ Да (Yes)** — the list is correct, nothing changes
- **❌ Нет (No)** — the passenger list is cleared, you'll need to re-add current passengers via **"👥 Добавить пассажиров"**

⚠️ **Important:** if you don't respond within **two hours**, your entire record will be deleted automatically! You'll have to press **"🚗 Стать водителем"** again and re-enter your car/plate. If something went wrong — message @ldrahrep!

⚠️ **Important:** if you answer **Нет (No)**, all your passengers will be deleted. Re-add them.

---

## FAQ

### I forgot to register passengers in time
Message the administrator. He can retroactively mark the correct days.

### A passenger switched to me from another car
The previous driver must first remove them (**🧑‍🤝‍🧑 Удалить пассажира**), then you can add them. Don't delay! If you don't know whose passengers they are, contact @ldrahrep

### My shift changed
Notify @ldrahrep! He will make the changes. Also don't delay!

### A passenger quit / left
Remove them via **🧑‍🤝‍🧑 Удалить пассажира**.

### I don't see the buttons
Type `/start` — the keyboard will refresh.

### The bot is not responding
Wait a minute and try again. If it didn't help — message @ldrahrep.

---

## Do NOT do this

- ❌ Don't send arbitrary messages to the bot — it won't understand, use buttons only
- ❌ Don't list passengers you don't actually drive — it's fraud and is verified against the timesheet
- ❌ Don't add passengers from a different shift — the bot will reject them
- ❌ Don't forget to remove passengers who no longer ride with you

---

## Important Reminders

✅ **Update the bot immediately** when something changes — who rides with you today. All changes must be made in the bot before 9pm the same day! If you missed the deadline, message @ldrahrep.
✅ **Check names** — an extra space or typo and the bot won't find them
✅ **Answer the weekly check** honestly — fair pay depends on it

## ⚠️ Main Rule

**This is primarily your responsibility** — to notify @ldrahrep and the bot about changes in a timely manner:

- **New passenger added?** → immediately press **"👥 Добавить пассажиров"**
- **Passenger stopped riding with you?** → immediately press **"🧑‍🤝‍🧑 Удалить пассажира"**
- **Passenger switched to another driver?** → immediately remove them from your list (otherwise that driver won't be able to add them)
- **Your shift / car / plate / role changed?** → immediately message **@ldrahrep**
- **Your passenger quit / left?** → immediately remove them from the list

**Pay is calculated based on the data you enter into the bot.** If you forgot to add someone — the day won't count. If you forgot to remove someone — it will be flagged as an anomaly and investigated.

🕒 **All changes for the day must be in the bot by 21:00** — at this time the system takes a snapshot of the state. Changes after 21:00 will go into the next day.

Colleagues! I know there have been many changes in the last few days, so for those days please message me at @ldrahrep in the following format:
First name, Last name, shift and location where the driver worked
First and last names of passengers
On which days you drove the passengers
I will only take corrections into account if you deliver the information correctly and on time!
