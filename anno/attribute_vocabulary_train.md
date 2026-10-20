# 标注词表

- 条目数：714
- 来源：D:\OneDrive\Pythoncode\rec_data\anno\annotations.json
- 划分：train
- 构建时间：2026-10-19T19:15:54

`scene_relative` 标记表示该取值绑在这张图的布局上（如 `in the first left queue`），step4 不会把它当作补充候选，但保留在词表里。

## apple

| 维度     | 谓词 id                                   | 写法                     | 极性     | 作用域   | 根数 | 实例数 | 出现写法                 |
|----------|-------------------------------------------|--------------------------|----------|----------|------|--------|--------------------------|
| color    | `apple/color/green`                       | green                    | positive | absolute | 11   | 320    | green                    |
| color    | `apple/color/red`                         | red                      | positive | absolute | 11   | 484    | red                      |
| color    | `apple/color/yellow`                      | yellow                   | positive | absolute | 2    | 14     | yellow                   |
| location | `apple/location/in-the-back-compartment`  | in the back compartment  | positive | absolute | 1    | 13     | in the back compartment  |
| location | `apple/location/in-the-bottom-center-box` | in the bottom center box | positive | absolute | 1    | 36     | in the bottom center box |
| location | `apple/location/in-the-bottom-left-bag`   | in the bottom left bag   | positive | absolute | 1    | 18     | in the bottom left bag   |
| location | `apple/location/in-the-front-compartment` | in the front compartment | positive | absolute | 1    | 16     | in the front compartment |
| location | `apple/location/in-the-top-bag`           | in the top bag           | positive | absolute | 1    | 17     | in the top bag           |
| location | `apple/location/in-the-top-right-box`     | in the top right box     | positive | absolute | 1    | 35     | in the top right box     |

## bag

| 维度     | 谓词 id                                     | 写法                         | 极性     | 作用域         | 根数 | 实例数 | 出现写法                     |
|----------|---------------------------------------------|------------------------------|----------|----------------|------|--------|------------------------------|
| location | `bag/location/in-the-two-leftmost-columns`  | in the two leftmost columns  | positive | scene_relative | 1    | 30     | in the two leftmost columns  |
| location | `bag/location/in-the-two-rightmost-columns` | in the two rightmost columns | positive | scene_relative | 1    | 24     | in the two rightmost columns |

## ball

| 维度     | 谓词 id                                              | 写法                                 | 极性     | 作用域   | 根数 | 实例数 | 出现写法                             |
|----------|------------------------------------------------------|--------------------------------------|----------|----------|------|--------|--------------------------------------|
| color    | `ball/color/black`                                   | black                                | positive | absolute | 1    | 13     | black                                |
| color    | `ball/color/blue`                                    | blue                                 | positive | absolute | 4    | 31     | blue                                 |
| color    | `ball/color/red`                                     | red                                  | positive | absolute | 4    | 43     | red                                  |
| color    | `ball/color/white`                                   | white                                | positive | absolute | 2    | 23     | white                                |
| color    | `ball/color/yellow`                                  | yellow                               | positive | absolute | 2    | 19     | yellow                               |
| location | `ball/location/to-the-left-of-the-right-angle-line`  | to the left of the right-angle line  | positive | absolute | 1    | 4      | to the left of the right-angle line  |
| location | `ball/location/to-the-right-of-the-right-angle-line` | to the right of the right-angle line | positive | absolute | 1    | 4      | to the right of the right-angle line |

## banana

| 维度     | 谓词 id                        | 写法         | 极性     | 作用域   | 根数 | 实例数 | 出现写法     |
|----------|--------------------------------|--------------|----------|----------|------|--------|--------------|
| location | `banana/location/in-the-bowl`  | in the bowl  | positive | absolute | 1    | 8      | in the bowl  |
| location | `banana/location/on-the-table` | on the table | positive | absolute | 1    | 10     | on the table |

## bead

| 维度     | 谓词 id                                 | 写法                    | 极性     | 作用域   | 根数 | 实例数 | 出现写法                |
|----------|-----------------------------------------|-------------------------|----------|----------|------|--------|-------------------------|
| color    | `bead/color/black`                      | black                   | positive | absolute | 3    | 136    | black                   |
| color    | `bead/color/brown`                      | brown                   | positive | absolute | 1    | 90     | brown                   |
| color    | `bead/color/cyan`                       | cyan                    | positive | absolute | 1    | 36     | cyan                    |
| color    | `bead/color/golden`                     | golden                  | positive | absolute | 2    | 34     | golden                  |
| color    | `bead/color/green`                      | green                   | positive | absolute | 1    | 40     | green                   |
| color    | `bead/color/light-blue`                 | light blue              | positive | absolute | 1    | 36     | light blue              |
| color    | `bead/color/purple`                     | purple                  | positive | absolute | 1    | 109    | purple                  |
| color    | `bead/color/silver`                     | silver                  | positive | absolute | 1    | 7      | silver                  |
| color    | `bead/color/white`                      | white                   | positive | absolute | 2    | 54     | white                   |
| location | `bead/location/in-the-innermost-circle` | in the innermost circle | positive | absolute | 1    | 27     | in the innermost circle |
| location | `bead/location/in-the-outermost-circle` | in the outermost circle | positive | absolute | 1    | 46     | in the outermost circle |
| material | `bead/material/wooden`                  | wooden                  | positive | absolute | 1    | 7      | wooden                  |

## bird

| 维度        | 谓词 id                                   | 写法                   | 极性     | 作用域         | 根数 | 实例数 | 出现写法               |
|-------------|-------------------------------------------|------------------------|----------|----------------|------|--------|------------------------|
| action      | `bird/action/flying`                      | flying                 | positive | absolute       | 2    | 4      | flying                 |
| action      | `bird/action/standing`                    | standing               | positive | absolute       | 1    | 6      | standing               |
| color       | `bird/color/black`                        | black                  | positive | absolute       | 1    | 9      | black                  |
| color       | `bird/color/white`                        | white                  | positive | absolute       | 1    | 6      | white                  |
| location    | `bird/location/on-the-line/verb=standing` | standing on the line   | positive | absolute       | 1    | 8      | standing on the line   |
| location    | `bird/location/on-the-second-top-line`    | on the second top line | positive | scene_relative | 1    | 10     | on the second top line |
| location    | `bird/location/on-the-third-top-line`     | on the third top line  | positive | scene_relative | 1    | 3      | on the third top line  |
| orientation | `bird/orientation/swimming-to-the-left`   | swimming to the left   | positive | absolute       | 1    | 4      | swimming to the left   |
| orientation | `bird/orientation/swimming-to-the-right`  | swimming to the right  | positive | absolute       | 1    | 11     | swimming to the right  |

## biscuit

| 维度  | 谓词 id                | 写法   | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|------------------------|--------|----------|----------|------|--------|----------|
| color | `biscuit/color/black`  | black  | positive | absolute | 1    | 12     | black    |
| color | `biscuit/color/white`  | white  | positive | absolute | 1    | 12     | white    |
| color | `biscuit/color/yellow` | yellow | positive | absolute | 1    | 12     | yellow   |

## boat

| 维度     | 谓词 id                                     | 写法                        | 极性     | 作用域   | 根数 | 实例数 | 出现写法                    |
|----------|---------------------------------------------|-----------------------------|----------|----------|------|--------|-----------------------------|
| location | `boat/location/parked-along-the-left-dock`  | parked along the left dock  | positive | absolute | 1    | 41     | parked along the left dock  |
| location | `boat/location/parked-along-the-right-dock` | parked along the right dock | positive | absolute | 1    | 24     | parked along the right dock |

## book

| 维度        | 谓词 id                                                   | 写法                                         | 极性     | 作用域         | 根数 | 实例数 | 出现写法                                     |
|-------------|-----------------------------------------------------------|----------------------------------------------|----------|----------------|------|--------|----------------------------------------------|
| color       | `book/color/bluish`                                       | bluish                                       | positive | absolute       | 3    | 33     | bluish                                       |
| color       | `book/color/greenish`                                     | greenish                                     | positive | absolute       | 1    | 15     | greenish                                     |
| color       | `book/color/reddish`                                      | reddish                                      | positive | absolute       | 3    | 30     | reddish                                      |
| color       | `book/color/with-the-title-printed-in-blue-on-the-spine`  | with the title printed in blue on the spine  | positive | absolute       | 1    | 5      | with the title printed in blue on the spine  |
| color       | `book/color/with-the-title-printed-in-green-on-the-spine` | with the title printed in green on the spine | positive | absolute       | 1    | 5      | with the title printed in green on the spine |
| color       | `book/color/with-the-title-printed-in-red-on-the-spine`   | with the title printed in red on the spine   | positive | absolute       | 1    | 9      | with the title printed in red on the spine   |
| color       | `book/color/yellowish`                                    | yellowish                                    | positive | absolute       | 1    | 7      | yellowish                                    |
| location    | `book/location/in-the-first-bottom-row`                   | in the first bottom row                      | positive | scene_relative | 1    | 6      | in the first bottom row]                     |
| location    | `book/location/in-the-first-top-row`                      | in the first top row                         | positive | scene_relative | 1    | 20     | in the first top row                         |
| location    | `book/location/in-the-highest-stack`                      | in the highest stack                         | positive | absolute       | 1    | 25     | in the highest stack                         |
| location    | `book/location/in-the-leftmost-stack`                     | in the leftmost stack                        | positive | scene_relative | 1    | 19     | in the leftmost stack                        |
| location    | `book/location/in-the-lowest-stack`                       | in the lowest stack                          | positive | absolute       | 1    | 9      | in the lowest stack                          |
| location    | `book/location/in-the-second-top-row`                     | in the second top row                        | positive | scene_relative | 2    | 19     | in the second top row                        |
| location    | `book/location/in-the-sixth-left-stack`                   | in the sixth left stack                      | positive | scene_relative | 1    | 18     | in the sixth left stack                      |
| location    | `book/location/on-the-bottom-shelf`                       | on the bottom shelf                          | positive | absolute       | 3    | 75     | on the bottom shelf                          |
| location    | `book/location/on-the-highest-layer`                      | on the highest layer                         | positive | absolute       | 1    | 79     | on the highest layer                         |
| location    | `book/location/on-the-lowest-layer`                       | on the lowest layer                          | positive | absolute       | 1    | 92     | on the lowest layer                          |
| location    | `book/location/on-the-top-shelf`                          | on the top shelf                             | positive | absolute       | 3    | 78     | on the top shelf                             |
| orientation | `book/orientation/in-horizontal-position`                 | in horizontal position                       | positive | absolute       | 1    | 36     | in horizontal position                       |
| orientation | `book/orientation/in-vertical-position`                   | in vertical position                         | positive | absolute       | 1    | 80     | in vertical position                         |
| variety     | `book/variety/accounting`                                 | accounting                                   | positive | absolute       | 1    | 3      | accounting                                   |
| variety     | `book/variety/advanced-accounting`                        | advanced accounting                          | positive | absolute       | 1    | 3      | advanced accounting                          |

## bottle

| 维度     | 谓词 id                                           | 写法                            | 极性     | 作用域   | 根数 | 实例数 | 出现写法                        |
|----------|---------------------------------------------------|---------------------------------|----------|----------|------|--------|---------------------------------|
| color    | `bottle/color/blue`                               | blue                            | positive | absolute | 1    | 20     | blue                            |
| color    | `bottle/color/green`                              | green                           | positive | absolute | 1    | 26     | green                           |
| color    | `bottle/color/red`                                | red                             | positive | absolute | 1    | 38     | red                             |
| color    | `bottle/color/yellow`                             | yellow                          | positive | absolute | 1    | 70     | yellow                          |
| location | `bottle/location/in-the-bottom-right-compartment` | in the bottom right compartment | positive | absolute | 1    | 12     | in the bottom right compartment |
| location | `bottle/location/in-the-top-left-compartment`     | in the top left compartment     | positive | absolute | 1    | 10     | in the top left compartment     |

## bottle cap

| 维度  | 谓词 id                   | 写法   | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|---------------------------|--------|----------|----------|------|--------|----------|
| color | `bottle-cap/color/blue`   | blue   | positive | absolute | 2    | 19     | blue     |
| color | `bottle-cap/color/pink`   | pink   | positive | absolute | 2    | 8      | pink     |
| color | `bottle-cap/color/white`  | white  | positive | absolute | 2    | 37     | White    |
| color | `bottle-cap/color/yellow` | yellow | positive | absolute | 2    | 15     | yellow   |

## bowl

| 维度     | 谓词 id                                  | 写法                     | 极性     | 作用域         | 根数 | 实例数 | 出现写法                 |
|----------|------------------------------------------|--------------------------|----------|----------------|------|--------|--------------------------|
| color    | `bowl/color/blue`                        | blue                     | positive | absolute       | 1    | 5      | blue                     |
| color    | `bowl/color/red`                         | red                      | positive | absolute       | 1    | 3      | red                      |
| color    | `bowl/color/white`                       | white                    | positive | absolute       | 1    | 7      | white                    |
| location | `bowl/location/in-the-leftmost-stack`    | in the leftmost stack    | positive | scene_relative | 1    | 15     | in the leftmost stack    |
| location | `bowl/location/in-the-second-left-stack` | in the second left stack | positive | scene_relative | 1    | 15     | in the second left stack |

## box

| 维度     | 谓词 id                                   | 写法                        | 极性     | 作用域         | 根数 | 实例数 | 出现写法                    |
|----------|-------------------------------------------|-----------------------------|----------|----------------|------|--------|-----------------------------|
| color    | `box/color/blue`                          | blue                        | positive | absolute       | 7    | 162    | blue                        |
| color    | `box/color/brown`                         | brown                       | positive | absolute       | 1    | 20     | brown                       |
| color    | `box/color/green`                         | green                       | positive | absolute       | 4    | 93     | green                       |
| color    | `box/color/purple`                        | purple                      | positive | absolute       | 2    | 9      | purple                      |
| color    | `box/color/red`                           | red                         | positive | absolute       | 8    | 116    | red                         |
| color    | `box/color/yellow`                        | yellow                      | positive | absolute       | 9    | 107    | yellow                      |
| location | `box/location/in-the-bottom-layer`        | in the bottom layer         | positive | absolute       | 10   | 144    | in the bottom layer         |
| location | `box/location/in-the-first-top-row`       | in the first top row        | positive | scene_relative | 1    | 6      | in the first top row        |
| location | `box/location/in-the-fourth-left-column`  | in the fourth left column   | positive | scene_relative | 1    | 4      | in the fourth left column   |
| location | `box/location/in-the-middle-layer`        | in the middle layer         | positive | absolute       | 8    | 81     | in the middle layer         |
| location | `box/location/in-the-second-bottom-layer` | in the second bottom layer  | positive | scene_relative | 1    | 16     | in the second bottom layer  |
| location | `box/location/in-the-second-top-layer`    | in the second top layer     | positive | scene_relative | 3    | 52     | in the second top layer     |
| location | `box/location/in-the-third-bottom-layer`  | in the third bottom layer   | positive | scene_relative | 2    | 21     | in the third bottom layer   |
| location | `box/location/in-the-third-top-layer`     | in the third top layer      | positive | scene_relative | 1    | 11     | in the third top layer      |
| location | `box/location/in-the-top-layer`           | in the top layer            | positive | absolute       | 10   | 96     | in the top layer            |
| variety  | `box/variety/containing-blueberries`      | containing blueberries      | positive | absolute       | 1    | 13     | containing blueberries      |
| variety  | `box/variety/containing-melon-slices`     | containing melon slices     | positive | absolute       | 1    | 4      | containing melon slices     |
| variety  | `box/variety/containing-pineapple-slices` | containing pineapple slices | positive | absolute       | 1    | 12     | containing pineapple slices |

## brick

| 维度  | 谓词 id              | 写法   | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|----------------------|--------|----------|----------|------|--------|----------|
| color | `brick/color/red`    | red    | positive | absolute | 1    | 60     | red      |
| color | `brick/color/yellow` | yellow | positive | absolute | 1    | 199    | yellow   |

## bullet

| 维度     | 谓词 id                             | 写法              | 极性     | 作用域         | 根数 | 实例数 | 出现写法          |
|----------|-------------------------------------|-------------------|----------|----------------|------|--------|-------------------|
| location | `bullet/location/in-the-first-row`  | in the first row  | positive | scene_relative | 1    | 10     | in the first row  |
| location | `bullet/location/in-the-second-row` | in the second row | positive | scene_relative | 1    | 10     | in the second row |

## camel

| 维度   | 谓词 id                 | 写法     | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|--------|-------------------------|----------|----------|----------|------|--------|----------|
| action | `camel/action/sitting`  | sitting  | positive | absolute | 2    | 9      | sitting  |
| action | `camel/action/standing` | standing | positive | absolute | 2    | 28     | standing |

## can

| 维度     | 谓词 id                               | 写法                   | 极性     | 作用域         | 根数 | 实例数 | 出现写法               |
|----------|---------------------------------------|------------------------|----------|----------------|------|--------|------------------------|
| color    | `can/color/black`                     | black                  | positive | absolute       | 1    | 7      | black                  |
| color    | `can/color/bluish`                    | bluish                 | positive | absolute       | 1    | 21     | bluish                 |
| color    | `can/color/golden`                    | golden                 | positive | absolute       | 1    | 43     | golden                 |
| color    | `can/color/green`                     | green                  | positive | absolute       | 1    | 4      | green                  |
| color    | `can/color/greenish`                  | greenish               | positive | absolute       | 1    | 39     | greenish               |
| color    | `can/color/reddish`                   | reddish                | positive | absolute       | 1    | 48     | reddish                |
| color    | `can/color/white`                     | white                  | positive | absolute       | 2    | 59     | white                  |
| location | `can/location/in-the-bottom-row`      | in the bottom row      | positive | absolute       | 1    | 12     | in the bottom row      |
| location | `can/location/in-the-bottom-shelf`    | in the bottom shelf    | positive | absolute       | 1    | 71     | in the bottom shelf    |
| location | `can/location/in-the-bottom-two-rows` | in the bottom two rows | positive | scene_relative | 1    | 36     | in the bottom two rows |
| location | `can/location/in-the-middle-row`      | in the middle row      | positive | absolute       | 2    | 30     | in the middle row      |
| location | `can/location/in-the-top-row`         | in the top row         | positive | absolute       | 3    | 34     | in the top row         |
| location | `can/location/in-the-top-shelf`       | in the top shelf       | positive | absolute       | 1    | 20     | in the top shelf       |
| location | `can/location/in-the-top-two-rows`    | in the top two rows    | positive | scene_relative | 1    | 30     | in the top two rows    |
| location | `can/location/on-the-bottom-shelf`    | on the bottom shelf    | positive | absolute       | 2    | 48     | on the bottom shelf    |
| location | `can/location/on-the-top-shelf`       | on the top shelf       | positive | absolute       | 2    | 63     | on the top shelf       |

## candle

| 维度     | 谓词 id                                       | 写法                        | 极性     | 作用域         | 根数 | 实例数 | 出现写法                    |
|----------|-----------------------------------------------|-----------------------------|----------|----------------|------|--------|-----------------------------|
| location | `candle/location/in-the-back`                 | in the back                 | positive | absolute       | 1    | 5      | in the back                 |
| location | `candle/location/in-the-front`                | in the front                | positive | absolute       | 1    | 8      | in the front                |
| location | `candle/location/in-the-further-away-cluster` | in the further away cluster | positive | absolute       | 1    | 12     | in the further away cluster |
| location | `candle/location/in-the-innermost-stripe`     | in the innermost stripe     | positive | absolute       | 1    | 5      | in the innermost stripe     |
| location | `candle/location/in-the-left-container`       | in the left container       | positive | absolute       | 1    | 18     | in the left container       |
| location | `candle/location/in-the-left-most-container`  | in the left most container  | positive | absolute       | 1    | 5      | in the left most container  |
| location | `candle/location/in-the-nearer-cluster`       | in the nearer cluster       | positive | absolute       | 1    | 19     | in the nearer cluster       |
| location | `candle/location/in-the-outermost-stripe`     | in the outermost stripe     | positive | absolute       | 1    | 9      | in the outermost stripe     |
| location | `candle/location/in-the-right-container`      | in the right container      | positive | absolute       | 1    | 47     | in the right container      |
| location | `candle/location/in-the-right-most-container` | in the right most container | positive | absolute       | 1    | 12     | in the right most container |
| location | `candle/location/in-the-second-top-layer`     | in the second top layer     | positive | scene_relative | 1    | 8      | in the second top layer     |
| location | `candle/location/in-the-top-layer`            | in the top layer            | positive | absolute       | 1    | 7      | in the top layer            |
| location | `candle/location/on-the-left`                 | on the left                 | positive | absolute       | 1    | 14     | on the left                 |
| location | `candle/location/on-the-right`                | on the right                | positive | absolute       | 1    | 10     | on the right                |
| size     | `candle/size/higher`                          | higher                      | positive | absolute       | 2    | 17     | higher                      |
| size     | `candle/size/shorter`                         | shorter                     | positive | absolute       | 2    | 33     | shorter                     |

## cap

| 维度     | 谓词 id                                                                | 写法                                                    | 极性     | 作用域         | 根数 | 实例数 | 出现写法                                                |
|----------|------------------------------------------------------------------------|---------------------------------------------------------|----------|----------------|------|--------|---------------------------------------------------------|
| location | `cap/location/black-in-the-rightmost-shelf`                            | black in the rightmost shelf                            | positive | scene_relative | 1    | 14     | black in the rightmost shelf                            |
| location | `cap/location/in-the-back-two-rows`                                    | in the back two rows                                    | positive | scene_relative | 1    | 9      | in the back two rows                                    |
| location | `cap/location/in-the-bottom-two-layers`                                | in the bottom two layers                                | positive | scene_relative | 1    | 13     | in the bottom two layers                                |
| location | `cap/location/in-the-bottom-two-rows`                                  | in the bottom two rows                                  | positive | scene_relative | 4    | 78     | in the bottom two rows                                  |
| location | `cap/location/in-the-bottom-two-rows-in-the-middle-and-right-sections` | in the bottom two rows in the middle and right sections | positive | scene_relative | 1    | 16     | in the bottom two rows in the middle and right sections |
| location | `cap/location/in-the-farthest-back-two-rows`                           | in the farthest back two rows                           | positive | scene_relative | 2    | 29     | in the farthest back two rows                           |
| location | `cap/location/in-the-first-left-three-columns`                         | in the first left three columns                         | positive | scene_relative | 1    | 31     | in the first left three columns                         |
| location | `cap/location/in-the-first-top-two-rows`                               | in the first top two rows                               | positive | scene_relative | 5    | 96     | in the first top two rows                               |
| location | `cap/location/in-the-front-two-rows`                                   | in the front two rows                                   | positive | scene_relative | 3    | 31     | in the front two rows                                   |
| location | `cap/location/in-the-left-two-columns`                                 | in the left two columns                                 | positive | scene_relative | 1    | 14     | in the left two columns                                 |
| location | `cap/location/in-the-right-three-columns`                              | in the right three columns                              | positive | scene_relative | 1    | 24     | in the right three columns                              |
| location | `cap/location/in-the-right-two-columns`                                | in the right two columns                                | positive | scene_relative | 1    | 12     | in the right two columns                                |
| location | `cap/location/in-the-second-left-column`                               | in the second left column                               | positive | scene_relative | 1    | 8      | in the second left column                               |
| location | `cap/location/in-the-top-row`                                          | in the top row                                          | positive | absolute       | 1    | 8      | in the top row                                          |
| location | `cap/location/in-the-top-three-layers`                                 | in the top three layers                                 | positive | scene_relative | 1    | 23     | in the top three layers                                 |
| location | `cap/location/in-the-top-two-layers-in-the-middle-section`             | in the top two layers in the middle section             | positive | scene_relative | 1    | 8      | in the top two layers in the middle section             |
| location | `cap/location/in-the-top-two-rows`                                     | in the top two rows                                     | positive | scene_relative | 1    | 10     | in the top two rows                                     |
| location | `cap/location/on-the-third-top-shelf`                                  | on the third top shelf                                  | positive | scene_relative | 1    | 12     | on the third top shelf                                  |
| location | `cap/location/on-the-top-two-shelves`                                  | on the top two shelves                                  | positive | scene_relative | 1    | 44     | on the top two shelves                                  |
| location | `cap/location/red-in-the-leftmost-shelf`                               | red in the leftmost shelf                               | positive | scene_relative | 1    | 12     | red in the leftmost shelf                               |

## capsule

| 维度     | 谓词 id                                      | 写法                      | 极性     | 作用域   | 根数 | 实例数 | 出现写法                  |
|----------|----------------------------------------------|---------------------------|----------|----------|------|--------|---------------------------|
| color    | `capsule/color/blue`                         | blue                      | positive | absolute | 2    | 36     | blue                      |
| color    | `capsule/color/blue-white`                   | blue white                | positive | absolute | 1    | 33     | blue white                |
| color    | `capsule/color/brown`                        | brown                     | positive | absolute | 1    | 13     | brown                     |
| color    | `capsule/color/pink`                         | pink                      | positive | absolute | 1    | 6      | pink                      |
| color    | `capsule/color/red`                          | red                       | positive | absolute | 1    | 41     | red                       |
| color    | `capsule/color/red-white`                    | red white                 | positive | absolute | 1    | 37     | red white                 |
| color    | `capsule/color/yellow`                       | yellow                    | positive | absolute | 1    | 8      | yellow                    |
| color    | `capsule/color/yellow-brown`                 | yellow brown              | positive | absolute | 1    | 33     | yellow brown              |
| location | `capsule/location/in-the-center`             | in the center             | positive | absolute | 1    | 2      | in the center             |
| location | `capsule/location/in-the-circular-formation` | in the circular formation | positive | absolute | 1    | 16     | in the circular formation |

## car

| 维度        | 谓词 id                                              | 写法                             | 极性     | 作用域   | 根数 | 实例数 | 出现写法                         |
|-------------|------------------------------------------------------|----------------------------------|----------|----------|------|--------|----------------------------------|
| action      | `car/action/changing-lane`                           | changing lane                    | positive | absolute | 327  | 601    | changing lane                    |
| action      | `car/action/making-a-turn`                           | making a turn                    | positive | absolute | 214  | 307    | making a turn                    |
| color       | `car/color/black`                                    | black                            | positive | absolute | 215  | 2958   | black                            |
| color       | `car/color/blue`                                     | blue                             | positive | absolute | 171  | 317    | blue                             |
| color       | `car/color/gray`                                     | gray                             | positive | absolute | 210  | 1664   | gray                             |
| color       | `car/color/green`                                    | green                            | positive | absolute | 93   | 124    | green                            |
| color       | `car/color/red`                                      | red                              | positive | absolute | 126  | 310    | red                              |
| color       | `car/color/silver`                                   | silver                           | positive | absolute | 1    | 15     | silver                           |
| color       | `car/color/white`                                    | white                            | positive | absolute | 217  | 3469   | white                            |
| color       | `car/color/yellow`                                   | yellow                           | positive | absolute | 86   | 113    | yellow                           |
| location    | `car/location/in-the-inner-lane`                     | in the inner lane                | positive | absolute | 1    | 20     | in the inner lane                |
| location    | `car/location/in-the-left-lane`                      | in the left lane                 | positive | absolute | 1    | 6      | in the left lane                 |
| location    | `car/location/in-the-outer-lane`                     | in the outer lane                | positive | absolute | 1    | 20     | in the outer lane                |
| location    | `car/location/in-the-right-lane`                     | in the right lane                | positive | absolute | 1    | 12     | in the right lane                |
| location    | `car/location/on-the-left-carriageway/verb=driving`  | driving on the left carriageway  | positive | absolute | 6    | 139    | driving on the left carriageway  |
| location    | `car/location/on-the-right-carriageway/verb=driving` | driving on the right carriageway | positive | absolute | 6    | 194    | driving on the right carriageway |
| orientation | `car/orientation/downward/verb=driving`              | driving downward                 | positive | absolute | 527  | 6802   | driving downward                 |
| orientation | `car/orientation/facing-backward`                    | facing backward                  | positive | absolute | 1    | 44     | facing backward                  |
| orientation | `car/orientation/facing-forward`                     | facing forward                   | positive | absolute | 1    | 71     | facing forward                   |
| orientation | `car/orientation/to-the-left/verb=driving`           | driving to the left              | positive | absolute | 583  | 6409   | driving to the left              |
| orientation | `car/orientation/to-the-right/verb=driving`          | driving to the right             | positive | absolute | 544  | 5322   | driving to the right             |
| orientation | `car/orientation/upward/verb=driving`                | driving upward                   | positive | absolute | 517  | 6221   | driving upward                   |

## carrom board piece

| 维度  | 谓词 id                          | 写法  | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|----------------------------------|-------|----------|----------|------|--------|----------|
| color | `carrom-board-piece/color/black` | black | positive | absolute | 4    | 36     | black    |
| color | `carrom-board-piece/color/brown` | brown | positive | absolute | 3    | 25     | brown    |
| color | `carrom-board-piece/color/red`   | red   | positive | absolute | 3    | 4      | red      |
| color | `carrom-board-piece/color/white` | white | positive | absolute | 2    | 12     | white    |

## cartridge

| 维度        | 谓词 id                                             | 写法                           | 极性     | 作用域         | 根数 | 实例数 | 出现写法                       |
|-------------|-----------------------------------------------------|--------------------------------|----------|----------------|------|--------|--------------------------------|
| color       | `cartridge/color/blue`                              | blue                           | positive | absolute       | 2    | 14     | blue                           |
| color       | `cartridge/color/orange`                            | orange                         | positive | absolute       | 1    | 8      | orange                         |
| color       | `cartridge/color/red`                               | red                            | positive | absolute       | 2    | 33     | red                            |
| color       | `cartridge/color/yellow`                            | yellow                         | positive | absolute       | 1    | 4      | yellow                         |
| location    | `cartridge/location/blue-in-the-second-top-layer`   | blue in the second top layer   | positive | scene_relative | 1    | 4      | blue in the second top layer   |
| location    | `cartridge/location/in-the-bottom-two-layers`       | in the bottom two layers       | positive | scene_relative | 1    | 24     | in the bottom two layers       |
| location    | `cartridge/location/in-the-first-left-column`       | in the first left column       | positive | scene_relative | 1    | 16     | in the first left column       |
| location    | `cartridge/location/in-the-first-top-row`           | in the first top row           | positive | scene_relative | 1    | 16     | in the first top row           |
| location    | `cartridge/location/in-the-sixth-to-ninth-top-rows` | in the sixth to ninth top rows | positive | scene_relative | 1    | 24     | in the sixth to ninth top rows |
| location    | `cartridge/location/in-the-third-and-fourth-rows`   | in the third and fourth rows   | positive | scene_relative | 1    | 32     | in the third and fourth rows   |
| location    | `cartridge/location/in-the-top-two-layers`          | in the top two layers          | positive | scene_relative | 1    | 24     | in the top two layers          |
| location    | `cartridge/location/red-in-the-lowest-layer`        | red in the lowest layer        | positive | absolute       | 1    | 7      | red in the lowest layer        |
| orientation | `cartridge/orientation/in-horizontal-position`      | in horizontal position         | positive | absolute       | 1    | 16     | in horizontal position         |
| orientation | `cartridge/orientation/in-slanted-position`         | in slanted position            | positive | absolute       | 1    | 8      | in slanted position            |
| orientation | `cartridge/orientation/in-vertical-position`        | in vertical position           | positive | absolute       | 1    | 17     | in vertical position           |

## cashew

| 维度     | 谓词 id                        | 写法         | 极性     | 作用域   | 根数 | 实例数 | 出现写法     |
|----------|--------------------------------|--------------|----------|----------|------|--------|--------------|
| location | `cashew/location/in-the-bowl`  | in the bowl  | positive | absolute | 1    | 50     | in the bowl  |
| location | `cashew/location/on-the-table` | on the table | positive | absolute | 1    | 26     | on the table |

## cassette

| 维度     | 谓词 id                                       | 写法                      | 极性     | 作用域         | 根数 | 实例数 | 出现写法                  |
|----------|-----------------------------------------------|---------------------------|----------|----------------|------|--------|---------------------------|
| location | `cassette/location/in-the-leftmost-column`    | in the leftmost column    | positive | scene_relative | 2    | 32     | in the leftmost column    |
| location | `cassette/location/in-the-leftmost-line`      | in the leftmost line      | positive | scene_relative | 1    | 13     | in the leftmost line      |
| location | `cassette/location/in-the-middle-column`      | in the middle column      | positive | absolute       | 1    | 17     | in the middle column      |
| location | `cassette/location/in-the-middle-two-lines`   | in the middle two lines   | positive | scene_relative | 1    | 28     | in the middle two lines   |
| location | `cassette/location/in-the-third-right-column` | in the third right column | positive | scene_relative | 1    | 19     | in the third right column |

## chair

| 维度     | 谓词 id                                      | 写法                        | 极性     | 作用域         | 根数 | 实例数 | 出现写法                    |
|----------|----------------------------------------------|-----------------------------|----------|----------------|------|--------|-----------------------------|
| color    | `chair/color/blue`                           | blue                        | positive | absolute       | 3    | 20     | blue                        |
| color    | `chair/color/red`                            | red                         | positive | absolute       | 3    | 38     | red                         |
| color    | `chair/color/white`                          | white                       | positive | absolute       | 3    | 31     | white                       |
| color    | `chair/color/yellow`                         | yellow                      | positive | absolute       | 2    | 14     | yellow                      |
| location | `chair/location/blue-in-the-first-two-rows`  | blue in the first two rows  | positive | scene_relative | 1    | 14     | blue in the first two rows  |
| location | `chair/location/in-the-middle-row`           | in the middle row           | positive | absolute       | 1    | 12     | in the middle row           |
| location | `chair/location/in-the-second-top-layer`     | in the second top layer     | positive | scene_relative | 1    | 9      | in the second top layer     |
| location | `chair/location/in-the-top-layer`            | in the top layer            | positive | absolute       | 1    | 9      | in the top layer            |
| location | `chair/location/in-the-topmost-row`          | in the topmost row          | positive | scene_relative | 1    | 17     | in the topmost row          |
| location | `chair/location/red-in-the-front-three-rows` | red in the front three rows | positive | scene_relative | 1    | 6      | red in the front three rows |

## chicken wing

| 维度     | 谓词 id                                   | 写法              | 极性     | 作用域         | 根数 | 实例数 | 出现写法          |
|----------|-------------------------------------------|-------------------|----------|----------------|------|--------|-------------------|
| location | `chicken-wing/location/in-the-first-row`  | in the first row  | positive | scene_relative | 1    | 5      | in the first row  |
| location | `chicken-wing/location/in-the-second-row` | in the second row | positive | scene_relative | 1    | 6      | in the second row |
| location | `chicken-wing/location/in-the-third-row`  | in the third row  | positive | scene_relative | 1    | 5      | in the third row  |

## chopstick

| 维度 | 谓词 id                  | 写法    | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|------|--------------------------|---------|----------|----------|------|--------|----------|
| size | `chopstick/size/longer`  | longer  | positive | absolute | 2    | 12     | longer   |
| size | `chopstick/size/shorter` | shorter | positive | absolute | 2    | 16     | shorter  |

## clothes

| 维度    | 谓词 id                 | 写法  | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|---------|-------------------------|-------|----------|----------|------|--------|----------|
| variety | `clothes/variety/shirt` | shirt | positive | absolute | 1    | 15     | shirt    |
| variety | `clothes/variety/suit`  | suit  | positive | absolute | 1    | 4      | suit     |

## coffee bean

| 维度  | 谓词 id                   | 写法  | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|---------------------------|-------|----------|----------|------|--------|----------|
| color | `coffee-bean/color/brown` | brown | positive | absolute | 1    | 34     | brown    |
| color | `coffee-bean/color/white` | white | positive | absolute | 1    | 13     | white    |

## coin

| 维度     | 谓词 id                | 写法   | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|----------|------------------------|--------|----------|----------|------|--------|----------|
| material | `coin/material/copper` | copper | positive | absolute | 12   | 481    | copper   |
| material | `coin/material/silver` | silver | positive | absolute | 12   | 505    | silver   |

## cotton ball

| 维度     | 谓词 id                             | 写法         | 极性     | 作用域   | 根数 | 实例数 | 出现写法     |
|----------|-------------------------------------|--------------|----------|----------|------|--------|--------------|
| location | `cotton-ball/location/on-the-left`  | on the left  | positive | absolute | 1    | 12     | on the left  |
| location | `cotton-ball/location/on-the-right` | on the right | positive | absolute | 1    | 12     | on the right |

## cow

| 维度  | 谓词 id                             | 写法                    | 极性     | 作用域   | 根数 | 实例数 | 出现写法                |
|-------|-------------------------------------|-------------------------|----------|----------|------|--------|-------------------------|
| color | `cow/color/black`                   | black                   | positive | absolute | 1    | 5      | black                   |
| color | `cow/color/black-and-white-spotted` | black and white spotted | positive | absolute | 1    | 15     | black and white spotted |

## crayon

| 维度     | 谓词 id                                | 写法                 | 极性     | 作用域         | 根数 | 实例数 | 出现写法             |
|----------|----------------------------------------|----------------------|----------|----------------|------|--------|----------------------|
| location | `crayon/location/in-the-bottom-row`    | in the bottom row    | positive | absolute       | 1    | 25     | in the bottom row    |
| location | `crayon/location/in-the-first-top-row` | in the first top row | positive | scene_relative | 1    | 25     | in the first top row |

## cup

| 维度     | 谓词 id                             | 写法                 | 极性     | 作用域         | 根数 | 实例数 | 出现写法             |
|----------|-------------------------------------|----------------------|----------|----------------|------|--------|----------------------|
| color    | `cup/color/black`                   | black                | positive | absolute       | 1    | 8      | black                |
| color    | `cup/color/blue`                    | blue                 | positive | absolute       | 2    | 60     | blue                 |
| color    | `cup/color/orange`                  | orange               | positive | absolute       | 1    | 3      | orange               |
| color    | `cup/color/red`                     | red                  | positive | absolute       | 2    | 173    | red                  |
| color    | `cup/color/white`                   | white                | positive | absolute       | 2    | 203    | white                |
| location | `cup/location/in-the-first-top-row` | in the first top row | positive | scene_relative | 1    | 39     | in the first top row |
| location | `cup/location/in-the-middle`        | in the middle        | positive | absolute       | 1    | 65     | in the middle        |
| variety  | `cup/variety/with-black-tea`        | with black tea       | positive | absolute       | 1    | 6      | with black tea       |
| variety  | `cup/variety/with-yellow-tea`       | with yellow tea      | positive | absolute       | 1    | 6      | with yellow tea      |

## donut

| 维度     | 谓词 id                               | 写法                 | 极性     | 作用域   | 根数 | 实例数 | 出现写法             |
|----------|---------------------------------------|----------------------|----------|----------|------|--------|----------------------|
| color    | `donut/color/black`                   | black                | positive | absolute | 1    | 2      | black                |
| color    | `donut/color/blue`                    | blue                 | positive | absolute | 1    | 18     | blue                 |
| color    | `donut/color/brown`                   | brown                | positive | absolute | 1    | 14     | brown                |
| color    | `donut/color/pink`                    | pink                 | positive | absolute | 1    | 5      | pink                 |
| color    | `donut/color/purple`                  | purple               | positive | absolute | 1    | 12     | purple               |
| color    | `donut/color/red`                     | red                  | positive | absolute | 1    | 12     | red                  |
| color    | `donut/color/yellow`                  | yellow               | positive | absolute | 2    | 21     | yellow               |
| location | `donut/location/on-the-left-section`  | on the left section  | positive | absolute | 1    | 25     | on the left section  |
| location | `donut/location/on-the-right-section` | on the right section | positive | absolute | 1    | 27     | on the right section |

## egg

| 维度     | 谓词 id                                             | 写法                                 | 极性     | 作用域         | 根数 | 实例数 | 出现写法                             |
|----------|-----------------------------------------------------|--------------------------------------|----------|----------------|------|--------|--------------------------------------|
| color    | `egg/color/brown`                                   | brown                                | positive | absolute       | 16   | 254    | brown                                |
| color    | `egg/color/white`                                   | white                                | positive | absolute       | 16   | 290    | white                                |
| location | `egg/location/in-the-basket`                        | in the basket                        | positive | absolute       | 1    | 13     | in the basket                        |
| location | `egg/location/in-the-first-top-three-rows`          | in the first top three rows          | positive | scene_relative | 1    | 24     | in the first top three rows          |
| location | `egg/location/in-the-left-bowl`                     | in the left bowl                     | positive | absolute       | 1    | 4      | in the left bowl                     |
| location | `egg/location/in-the-left-box`                      | in the left box                      | positive | absolute       | 1    | 11     | in the left box                      |
| location | `egg/location/in-the-left-four-columns`             | in the left four columns             | positive | scene_relative | 1    | 23     | in the left four columns             |
| location | `egg/location/in-the-leftmost-box`                  | in the leftmost box                  | positive | scene_relative | 1    | 12     | in the leftmost box                  |
| location | `egg/location/in-the-middle-highest-box`            | in the middle highest box            | positive | absolute       | 1    | 12     | in the middle highest box            |
| location | `egg/location/in-the-right-bowl`                    | in the right bowl                    | positive | absolute       | 1    | 7      | in the right bowl                    |
| location | `egg/location/in-the-right-box`                     | in the right box                     | positive | absolute       | 1    | 5      | in the right box                     |
| location | `egg/location/in-the-right-most-second-highest-box` | in the right most second highest box | positive | scene_relative | 1    | 8      | in the right most second highest box |
| location | `egg/location/in-the-rightmost-box`                 | in the rightmost box                 | positive | scene_relative | 1    | 12     | in the rightmost box                 |
| location | `egg/location/on-the-table`                         | on the table                         | positive | absolute       | 1    | 138    | on the table                         |

## finger food

| 维度     | 谓词 id                                   | 写法               | 极性     | 作用域   | 根数 | 实例数 | 出现写法           |
|----------|-------------------------------------------|--------------------|----------|----------|------|--------|--------------------|
| location | `finger-food/location/in-the-left-plate`  | in the left plate  | positive | absolute | 1    | 10     | in the left plate  |
| location | `finger-food/location/in-the-right-plate` | in the right plate | positive | absolute | 1    | 3      | in the right plate |

## fish

| 维度 | 谓词 id             | 写法    | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|------|---------------------|---------|----------|----------|------|--------|----------|
| size | `fish/size/bigger`  | bigger  | positive | absolute | 1    | 1      | bigger   |
| size | `fish/size/smaller` | smaller | positive | absolute | 1    | 169    | smaller  |

## flower

| 维度     | 谓词 id                                       | 写法                        | 极性     | 作用域         | 根数 | 实例数 | 出现写法                    |
|----------|-----------------------------------------------|-----------------------------|----------|----------------|------|--------|-----------------------------|
| color    | `flower/color/red`                            | red                         | positive | absolute       | 1    | 12     | red                         |
| color    | `flower/color/yellow`                         | yellow                      | positive | absolute       | 1    | 4      | yellow                      |
| location | `flower/location/green-in-the-top-two-rows`   | green in the top two rows   | positive | scene_relative | 1    | 20     | green in the top two rows   |
| location | `flower/location/white-in-the-top-three-rows` | white in the top three rows | positive | scene_relative | 1    | 9      | white in the top three rows |

## flower pot

| 维度     | 谓词 id                                      | 写法                   | 极性     | 作用域         | 根数 | 实例数 | 出现写法               |
|----------|----------------------------------------------|------------------------|----------|----------------|------|--------|------------------------|
| location | `flower-pot/location/in-the-bottom-two-rows` | in the bottom two rows | positive | scene_relative | 1    | 16     | in the bottom two rows |
| location | `flower-pot/location/in-the-second-top-row`  | in the second top row  | positive | scene_relative | 1    | 6      | in the second top row  |

## fruit

| 维度     | 谓词 id                       | 写法         | 极性     | 作用域   | 根数 | 实例数 | 出现写法     |
|----------|-------------------------------|--------------|----------|----------|------|--------|--------------|
| location | `fruit/location/on-the-table` | on the table | positive | absolute | 1    | 7      | on the table |

## fruit box

| 维度     | 谓词 id                                  | 写法                | 极性     | 作用域   | 根数 | 实例数 | 出现写法            |
|----------|------------------------------------------|---------------------|----------|----------|------|--------|---------------------|
| location | `fruit-box/location/in-the-bottom-layer` | in the bottom layer | positive | absolute | 2    | 46     | in the bottom layer |
| location | `fruit-box/location/in-the-top-layer`    | in the top layer    | positive | absolute | 2    | 47     | in the top layer    |

## glass ball

| 维度  | 谓词 id                   | 写法   | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|---------------------------|--------|----------|----------|------|--------|----------|
| color | `glass-ball/color/blue`   | blue   | positive | absolute | 1    | 4      | blue     |
| color | `glass-ball/color/green`  | green  | positive | absolute | 1    | 5      | green    |
| color | `glass-ball/color/white`  | white  | positive | absolute | 1    | 5      | white    |
| color | `glass-ball/color/yellow` | yellow | positive | absolute | 1    | 9      | yellow   |

## glasses

| 维度    | 谓词 id                       | 写法        | 极性     | 作用域   | 根数 | 实例数 | 出现写法    |
|---------|-------------------------------|-------------|----------|----------|------|--------|-------------|
| variety | `glasses/variety/sunglasses`  | sunglasses  | positive | absolute | 3    | 86     | sunglasses  |
| variety | `glasses/variety/transparent` | transparent | positive | absolute | 3    | 37     | transparent |

## go stone

| 维度  | 谓词 id                | 写法  | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|------------------------|-------|----------|----------|------|--------|----------|
| color | `go-stone/color/black` | black | positive | absolute | 17   | 1379   | black    |
| color | `go-stone/color/white` | white | positive | absolute | 17   | 1311   | white    |

## goods

| 维度     | 谓词 id                                  | 写法                    | 极性     | 作用域         | 根数 | 实例数 | 出现写法                |
|----------|------------------------------------------|-------------------------|----------|----------------|------|--------|-------------------------|
| location | `goods/location/on-the-highest-shelf`    | on the highest shelf    | positive | absolute       | 1    | 48     | on the highest shelf    |
| location | `goods/location/on-the-second-top-shelf` | on the second top shelf | positive | scene_relative | 1    | 38     | on the second top shelf |

## grape

| 维度     | 谓词 id                  | 写法    | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|----------|--------------------------|---------|----------|----------|------|--------|----------|
| color    | `grape/color/green`      | green   | positive | absolute | 3    | 147    | green    |
| color    | `grape/color/purple`     | purple  | positive | absolute | 3    | 105    | purple   |
| location | `grape/location/in-hand` | in hand | positive | absolute | 1    | 36     | in hand  |

## green pea

| 维度     | 谓词 id                                   | 写法                 | 极性     | 作用域   | 根数 | 实例数 | 出现写法             |
|----------|-------------------------------------------|----------------------|----------|----------|------|--------|----------------------|
| location | `green-pea/location/in-the-bowl`          | in the bowl          | positive | absolute | 1    | 80     | in the bowl          |
| location | `green-pea/location/in-the-cup-and-plate` | in the cup and plate | positive | absolute | 1    | 64     | in the cup and plate |
| location | `green-pea/location/on-the-table`         | on the table         | positive | absolute | 2    | 98     | on the table         |

## horse

| 维度  | 谓词 id             | 写法  | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|---------------------|-------|----------|----------|------|--------|----------|
| color | `horse/color/black` | black | positive | absolute | 1    | 3      | black    |
| color | `horse/color/brown` | brown | positive | absolute | 2    | 22     | brown    |
| color | `horse/color/white` | white | positive | absolute | 1    | 3      | white    |

## hot air balloon

| 维度  | 谓词 id                          | 写法     | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|----------------------------------|----------|----------|----------|------|--------|----------|
| color | `hot-air-balloon/color/colorful` | colorful | positive | absolute | 1    | 30     | colorful |
| color | `hot-air-balloon/color/white`    | white    | positive | absolute | 1    | 16     | white    |
| color | `hot-air-balloon/color/yellow`   | yellow   | positive | absolute | 1    | 16     | yellow   |

## ice cream

| 维度     | 谓词 id                                  | 写法                | 极性     | 作用域   | 根数 | 实例数 | 出现写法            |
|----------|------------------------------------------|---------------------|----------|----------|------|--------|---------------------|
| location | `ice-cream/location/in-the-bottom-layer` | in the bottom layer | positive | absolute | 1    | 16     | in the bottom layer |
| location | `ice-cream/location/in-the-top-layer`    | in the top layer    | positive | absolute | 1    | 14     | in the top layer    |

## ketchup

| 维度     | 谓词 id                                | 写法                | 极性     | 作用域   | 根数 | 实例数 | 出现写法            |
|----------|----------------------------------------|---------------------|----------|----------|------|--------|---------------------|
| location | `ketchup/location/on-the-bottom-shelf` | on the bottom shelf | positive | absolute | 1    | 26     | on the bottom shelf |
| location | `ketchup/location/on-the-middle-shelf` | on the middle shelf | positive | absolute | 1    | 11     | on the middle shelf |
| location | `ketchup/location/on-the-top-shelf`    | on the top shelf    | positive | absolute | 1    | 13     | on the top shelf    |
| variety  | `ketchup/variety/heinz`                | heinz               | positive | absolute | 1    | 32     | heinz               |
| variety  | `ketchup/variety/hunts`                | hunts               | positive | absolute | 1    | 17     | hunts               |

## key

| 维度  | 谓词 id            | 写法   | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|--------------------|--------|----------|----------|------|--------|----------|
| color | `key/color/blue`   | blue   | positive | absolute | 3    | 155    | blue     |
| color | `key/color/green`  | green  | positive | absolute | 1    | 34     | green    |
| color | `key/color/pink`   | pink   | positive | absolute | 2    | 20     | pink     |
| color | `key/color/purple` | purple | positive | absolute | 1    | 13     | purple   |
| color | `key/color/red`    | red    | positive | absolute | 1    | 25     | red      |
| color | `key/color/white`  | white  | positive | absolute | 1    | 54     | white    |
| color | `key/color/yellow` | yellow | positive | absolute | 2    | 55     | yellow   |

## kidney bean

| 维度     | 谓词 id                                     | 写法                 | 极性     | 作用域   | 根数 | 实例数 | 出现写法             |
|----------|---------------------------------------------|----------------------|----------|----------|------|--------|----------------------|
| location | `kidney-bean/location/in-the-left-cluster`  | in the left cluster  | positive | absolute | 1    | 80     | in the left cluster  |
| location | `kidney-bean/location/in-the-right-cluster` | in the right cluster | positive | absolute | 1    | 53     | in the right cluster |

## lego brick

| 维度  | 谓词 id                   | 写法   | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|---------------------------|--------|----------|----------|------|--------|----------|
| color | `lego-brick/color/blue`   | blue   | positive | absolute | 1    | 11     | blue     |
| color | `lego-brick/color/green`  | green  | positive | absolute | 1    | 8      | green    |
| color | `lego-brick/color/red`    | red    | positive | absolute | 1    | 14     | red      |
| color | `lego-brick/color/yellow` | yellow | positive | absolute | 1    | 10     | yellow   |

## lip stick

| 维度     | 谓词 id                                    | 写法                  | 极性     | 作用域         | 根数 | 实例数 | 出现写法              |
|----------|--------------------------------------------|-----------------------|----------|----------------|------|--------|-----------------------|
| color    | `lip-stick/color/bluish`                   | bluish                | positive | absolute       | 1    | 12     | bluish                |
| color    | `lip-stick/color/reddish`                  | reddish               | positive | absolute       | 1    | 25     | reddish               |
| color    | `lip-stick/color/yellowish`                | yellowish             | positive | absolute       | 1    | 4      | yellowish             |
| location | `lip-stick/location/in-the-first-top-row`  | in the first top row  | positive | scene_relative | 1    | 23     | in the first top row  |
| location | `lip-stick/location/in-the-second-top-row` | in the second top row | positive | scene_relative | 1    | 23     | in the second top row |

## milk bottle

| 维度        | 谓词 id                                        | 写法                    | 极性     | 作用域         | 根数 | 实例数 | 出现写法                |
|-------------|------------------------------------------------|-------------------------|----------|----------------|------|--------|-------------------------|
| location    | `milk-bottle/location/in-the-left-most-rack`   | in the left most rack   | positive | absolute       | 2    | 30     | in the left most rack   |
| location    | `milk-bottle/location/in-the-middle-rack`      | in the middle rack      | positive | absolute       | 1    | 16     | in the middle rack      |
| location    | `milk-bottle/location/in-the-second-left-rack` | in the second left rack | positive | scene_relative | 1    | 13     | in the second left rack |
| location    | `milk-bottle/location/on-the-bottom-shelf`     | on the bottom shelf     | positive | absolute       | 1    | 19     | on the bottom shelf     |
| location    | `milk-bottle/location/on-the-top-shelf`        | on the top shelf        | positive | absolute       | 1    | 52     | on the top shelf        |
| orientation | `milk-bottle/orientation/in-slanted-position`  | in slanted position     | positive | absolute       | 1    | 7      | in slanted position     |
| orientation | `milk-bottle/orientation/in-upright-position`  | in upright position     | positive | absolute       | 1    | 15     | in upright position     |

## naan bread

| 维度     | 谓词 id                            | 写法         | 极性     | 作用域   | 根数 | 实例数 | 出现写法     |
|----------|------------------------------------|--------------|----------|----------|------|--------|--------------|
| location | `naan-bread/location/in-hands`     | in hands     | positive | absolute | 1    | 19     | in hands     |
| location | `naan-bread/location/in-the-back`  | in the back  | positive | absolute | 1    | 19     | in the back  |
| location | `naan-bread/location/in-the-front` | in the front | positive | absolute | 1    | 15     | in the front |
| location | `naan-bread/location/on-the-table` | on the table | positive | absolute | 1    | 15     | on the table |

## nail polish

| 维度     | 谓词 id                                           | 写法                       | 极性     | 作用域         | 根数 | 实例数 | 出现写法                   |
|----------|---------------------------------------------------|----------------------------|----------|----------------|------|--------|----------------------------|
| color    | `nail-polish/color/bluish`                        | bluish                     | positive | absolute       | 2    | 34     | bluish                     |
| color    | `nail-polish/color/green`                         | green                      | positive | absolute       | 1    | 5      | green                      |
| color    | `nail-polish/color/greenish`                      | greenish                   | positive | absolute       | 1    | 8      | greenish                   |
| color    | `nail-polish/color/red`                           | red                        | positive | absolute       | 1    | 10     | red                        |
| color    | `nail-polish/color/reddish`                       | reddish                    | positive | absolute       | 3    | 98     | reddish                    |
| color    | `nail-polish/color/yellowish`                     | yellowish                  | positive | absolute       | 2    | 15     | yellowish                  |
| location | `nail-polish/location/in-the-bottom-layer`        | in the bottom layer        | positive | absolute       | 3    | 44     | in the bottom layer        |
| location | `nail-polish/location/in-the-fourth-top-layer`    | in the fourth top layer    | positive | scene_relative | 2    | 49     | in the fourth top layer    |
| location | `nail-polish/location/in-the-fourth-top-row`      | in the fourth top row      | positive | scene_relative | 1    | 27     | in the fourth top row      |
| location | `nail-polish/location/in-the-second-bottom-layer` | in the second bottom layer | positive | scene_relative | 6    | 110    | in the second bottom layer |
| location | `nail-polish/location/in-the-second-top-layer`    | in the second top layer    | positive | scene_relative | 8    | 173    | in the second top layer    |
| location | `nail-polish/location/in-the-second-top-row`      | in the second top row      | positive | scene_relative | 1    | 27     | in the second top row      |
| location | `nail-polish/location/in-the-sixth-top-row`       | in the sixth top row       | positive | scene_relative | 1    | 27     | in the sixth top row       |
| location | `nail-polish/location/in-the-third-bottom-layer`  | in the third bottom layer  | positive | scene_relative | 2    | 25     | in the third bottom layer  |
| location | `nail-polish/location/in-the-third-top-layer`     | in the third top layer     | positive | scene_relative | 5    | 89     | in the third top layer     |
| location | `nail-polish/location/in-the-top-layer`           | in the top layer           | positive | absolute       | 10   | 215    | in the top layer           |
| location | `nail-polish/location/in-the-top-row`             | in the top row             | positive | absolute       | 1    | 27     | in the top row             |

## pea

| 维度     | 谓词 id                               | 写法                   | 极性     | 作用域   | 根数 | 实例数 | 出现写法               |
|----------|---------------------------------------|------------------------|----------|----------|------|--------|------------------------|
| location | `pea/location/in-the-bottom-most-pod` | in the bottom most pod | positive | absolute | 1    | 9      | in the bottom most pod |
| location | `pea/location/in-the-pod`             | in the pod             | positive | absolute | 1    | 10     | in the pod             |
| location | `pea/location/in-the-top-most-pod`    | in the top most pod    | positive | absolute | 1    | 9      | in the top most pod    |
| location | `pea/location/scattered-on-the-table` | scattered on the table | positive | absolute | 1    | 15     | scattered on the table |

## peach

| 维度     | 谓词 id                            | 写法              | 极性     | 作用域   | 根数 | 实例数 | 出现写法          |
|----------|------------------------------------|-------------------|----------|----------|------|--------|-------------------|
| location | `peach/location/in-the-wooden-box` | in the wooden box | positive | absolute | 1    | 18     | in the wooden box |
| location | `peach/location/on-the-grass`      | on the grass      | positive | absolute | 1    | 32     | on the grass      |

## pearl

| 维度  | 谓词 id             | 写法  | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|---------------------|-------|----------|----------|------|--------|----------|
| color | `pearl/color/blue`  | blue  | positive | absolute | 1    | 11     | blue     |
| color | `pearl/color/white` | white | positive | absolute | 1    | 12     | white    |

## pen

| 维度     | 谓词 id                                           | 写法                               | 极性     | 作用域         | 根数 | 实例数 | 出现写法                           |
|----------|---------------------------------------------------|------------------------------------|----------|----------------|------|--------|------------------------------------|
| color    | `pen/color/blue`                                  | blue                               | positive | absolute       | 5    | 114    | blue                               |
| color    | `pen/color/green`                                 | green                              | positive | absolute       | 5    | 135    | green                              |
| color    | `pen/color/red`                                   | red                                | positive | absolute       | 5    | 124    | red                                |
| color    | `pen/color/yellow`                                | yellow                             | positive | absolute       | 5    | 61     | yellow                             |
| location | `pen/location/in-the-bottom-container`            | in the bottom container            | positive | absolute       | 1    | 32     | in the bottom container            |
| location | `pen/location/in-the-bottom-section`              | in the bottom section              | positive | absolute       | 1    | 21     | in the bottom section              |
| location | `pen/location/in-the-bottom-two-layers`           | in the bottom two layers           | positive | scene_relative | 1    | 24     | in the bottom two layers           |
| location | `pen/location/in-the-farthest-back-row`           | in the farthest back row           | positive | scene_relative | 1    | 18     | in the farthest back row           |
| location | `pen/location/in-the-first-top-layer`             | in the first top layer             | positive | scene_relative | 1    | 12     | in the first top layer             |
| location | `pen/location/in-the-front-row`                   | in the front row                   | positive | absolute       | 1    | 18     | in the front row                   |
| location | `pen/location/in-the-second-and-third-top-layers` | in the second and third top layers | positive | scene_relative | 1    | 24     | in the second and third top layers |
| location | `pen/location/in-the-top-container`               | in the top container               | positive | absolute       | 1    | 32     | in the top container               |
| location | `pen/location/in-the-top-section`                 | in the top section                 | positive | absolute       | 1    | 20     | in the top section                 |

## pencil

| 维度     | 谓词 id                                           | 写法                            | 极性     | 作用域         | 根数 | 实例数 | 出现写法                        |
|----------|---------------------------------------------------|---------------------------------|----------|----------------|------|--------|---------------------------------|
| color    | `pencil/color/black`                              | black                           | positive | absolute       | 2    | 22     | black                           |
| color    | `pencil/color/blue`                               | blue                            | positive | absolute       | 2    | 34     | blue                            |
| color    | `pencil/color/bluish`                             | bluish                          | positive | absolute       | 3    | 17     | bluish                          |
| color    | `pencil/color/grayish`                            | grayish                         | positive | absolute       | 2    | 12     | grayish                         |
| color    | `pencil/color/greenish`                           | greenish                        | positive | absolute       | 3    | 22     | greenish                        |
| color    | `pencil/color/red`                                | red                             | positive | absolute       | 1    | 9      | red                             |
| color    | `pencil/color/reddish`                            | reddish                         | positive | absolute       | 4    | 33     | reddish                         |
| color    | `pencil/color/yellowish`                          | yellowish                       | positive | absolute       | 3    | 17     | yellowish                       |
| location | `pencil/location/in-the-bottom-container`         | in the bottom container         | positive | absolute       | 2    | 70     | in the bottom container         |
| location | `pencil/location/in-the-left-most-compartment`    | in the left most compartment    | positive | absolute       | 1    | 10     | in the left most compartment    |
| location | `pencil/location/in-the-middle-container`         | in the middle container         | positive | absolute       | 1    | 40     | in the middle container         |
| location | `pencil/location/in-the-second-right-compartment` | in the second right compartment | positive | scene_relative | 1    | 10     | in the second right compartment |
| location | `pencil/location/in-the-top-container`            | in the top container            | positive | absolute       | 2    | 70     | in the top container            |

## penguin

| 维度     | 谓词 id                         | 写法         | 极性     | 作用域   | 根数 | 实例数 | 出现写法     |
|----------|---------------------------------|--------------|----------|----------|------|--------|--------------|
| action   | `penguin/action/lying-down`     | lying down   | positive | absolute | 1    | 24     | lying down   |
| action   | `penguin/action/standing`       | standing     | positive | absolute | 1    | 36     | standing     |
| location | `penguin/location/in-the-sea`   | in the sea   | positive | absolute | 1    | 6      | in the sea   |
| location | `penguin/location/on-the-beach` | on the beach | positive | absolute | 1    | 24     | on the beach |

## pepper

| 维度  | 谓词 id               | 写法   | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|-----------------------|--------|----------|----------|------|--------|----------|
| color | `pepper/color/orange` | orange | positive | absolute | 1    | 6      | orange   |
| color | `pepper/color/red`    | red    | positive | absolute | 2    | 7      | red      |
| color | `pepper/color/yellow` | yellow | positive | absolute | 2    | 13     | yellow   |

## person

| 维度        | 谓词 id                                                               | 写法                                                | 极性     | 作用域         | 根数 | 实例数 | 出现写法                                                         |
|-------------|-----------------------------------------------------------------------|-----------------------------------------------------|----------|----------------|------|--------|------------------------------------------------------------------|
| accessory   | `person/accessory/bag/verb=carrying/polarity=negative`                | not carrying bag                                    | negative | absolute       | 1    | 8      | not carrying a bag                                               |
| accessory   | `person/accessory/bag/verb=carrying`                                  | carrying bag                                        | positive | absolute       | 1    | 17     | carrying a bag                                                   |
| accessory   | `person/accessory/blue-balloons/verb=holding`                         | holding blue balloons                               | positive | absolute       | 1    | 9      | holding blue balloons                                            |
| accessory   | `person/accessory/blue-flower/verb=wearing`                           | wearing blue flower                                 | positive | absolute       | 1    | 11     | wearing a blue flower                                            |
| accessory   | `person/accessory/blue-tie/verb=wearing`                              | wearing blue tie                                    | positive | absolute       | 1    | 12     | wearing a blue tie                                               |
| accessory   | `person/accessory/book/verb=holding`                                  | holding book                                        | positive | absolute       | 1    | 39     | holding a book                                                   |
| accessory   | `person/accessory/flag/verb=holding/polarity=negative`                | not holding flag                                    | negative | absolute       | 1    | 51     | not holding a flag                                               |
| accessory   | `person/accessory/flag/verb=holding`                                  | holding flag                                        | positive | absolute       | 2    | 35     | holding a flag                                                   |
| accessory   | `person/accessory/green-hat/verb=wearing`                             | wearing green hat                                   | positive | absolute       | 1    | 20     | wearing a green hat                                              |
| accessory   | `person/accessory/green-umbrella/verb=holding`                        | holding green umbrella                              | positive | absolute       | 1    | 32     | holding a green umbrella                                         |
| accessory   | `person/accessory/hat/verb=wearing/polarity=negative`                 | not wearing hat                                     | negative | absolute       | 7    | 502    | not wearing a hat                                                |
| accessory   | `person/accessory/hat/verb=wearing`                                   | wearing hat                                         | positive | absolute       | 6    | 292    | wearing a hat                                                    |
| accessory   | `person/accessory/headscarf/verb=wearing/polarity=negative`           | not wearing headscarf                               | negative | absolute       | 1    | 10     | not wearing a headscarf                                          |
| accessory   | `person/accessory/headscarf/verb=wearing`                             | wearing headscarf                                   | positive | absolute       | 1    | 10     | wearing a headscarf                                              |
| accessory   | `person/accessory/helmet/verb=wearing/polarity=negative`              | not wearing helmet                                  | negative | absolute       | 1    | 19     | not wearing a helmet                                             |
| accessory   | `person/accessory/helmet/verb=wearing`                                | wearing helmet                                      | positive | absolute       | 2    | 43     | wearing a helmet                                                 |
| accessory   | `person/accessory/phone/verb=holding`                                 | holding phone                                       | positive | absolute       | 1    | 6      | holding a phone                                                  |
| accessory   | `person/accessory/pink-fan/verb=holding`                              | holding pink fan                                    | positive | absolute       | 1    | 21     | holding a pink fan                                               |
| accessory   | `person/accessory/red-flag/verb=holding`                              | holding red flag                                    | positive | absolute       | 1    | 57     | holding a red flag                                               |
| accessory   | `person/accessory/red-flower/verb=wearing`                            | wearing red flower                                  | positive | absolute       | 1    | 8      | wearing a red flower                                             |
| accessory   | `person/accessory/red-hat/verb=wearing`                               | wearing red hat                                     | positive | absolute       | 1    | 7      | wearing a red hat                                                |
| accessory   | `person/accessory/red-tie/verb=wearing`                               | wearing red tie                                     | positive | absolute       | 1    | 30     | wearing a red tie                                                |
| accessory   | `person/accessory/sunflower/verb=holding`                             | holding sunflower                                   | positive | absolute       | 1    | 67     | holding a sunflower                                              |
| accessory   | `person/accessory/umbrella/verb=holding/polarity=negative`            | not holding umbrella                                | negative | absolute       | 5    | 160    | not holding an umbrella, not holding a umbrella                  |
| accessory   | `person/accessory/umbrella/verb=holding`                              | holding umbrella                                    | positive | absolute       | 5    | 50     | holding an umbrella, holding a umbrella                          |
| accessory   | `person/accessory/yellow-balloons/verb=holding`                       | holding yellow balloons                             | positive | absolute       | 1    | 17     | holding yellow balloons                                          |
| accessory   | `person/accessory/yellow-hat/verb=wearing`                            | wearing yellow hat                                  | positive | absolute       | 1    | 43     | wearing a yellow hat                                             |
| action      | `person/action/another-person/verb=holding`                           | holding another person                              | positive | absolute       | 1    | 4      | holding another person                                           |
| action      | `person/action/crossing-the-road/polarity=negative`                   | not crossing the road                               | negative | absolute       | 1    | 128    | not crossing the road                                            |
| action      | `person/action/crossing-the-road`                                     | crossing the road                                   | positive | absolute       | 63   | 628    | crossing the road                                                |
| action      | `person/action/dancing`                                               | dancing                                             | positive | absolute       | 41   | 1508   | dancing                                                          |
| action      | `person/action/dragon-dancing`                                        | dragon dancing                                      | positive | absolute       | 1    | 53     | dragon dancing                                                   |
| action      | `person/action/eating`                                                | eating                                              | positive | absolute       | 1    | 89     | eating                                                           |
| action      | `person/action/exercising`                                            | exercising                                          | positive | absolute       | 16   | 284    | exercising                                                       |
| action      | `person/action/kneeling-on-one-knee`                                  | kneeling on one knee                                | positive | absolute       | 1    | 4      | kneeling on one knee                                             |
| action      | `person/action/lying-down`                                            | lying down                                          | positive | absolute       | 4    | 44     | lying down                                                       |
| action      | `person/action/playing-a-drum/polarity=negative`                      | not playing a drum                                  | negative | absolute       | 1    | 414    | not playing a drum                                               |
| action      | `person/action/playing-a-drum`                                        | playing a drum                                      | positive | absolute       | 2    | 32     | playing a drum                                                   |
| action      | `person/action/playing-a-musical-instrument`                          | playing a musical instrument                        | positive | absolute       | 1    | 67     | playing a musical instrument                                     |
| action      | `person/action/playing-basketball`                                    | playing basketball                                  | positive | absolute       | 3    | 21     | playing basketball                                               |
| action      | `person/action/playing-football`                                      | playing football                                    | positive | absolute       | 1    | 18     | playing football                                                 |
| action      | `person/action/riding-a-horse`                                        | riding a horse                                      | positive | absolute       | 1    | 5      | riding a horse                                                   |
| action      | `person/action/running`                                               | running                                             | positive | absolute       | 6    | 409    | running                                                          |
| action      | `person/action/sitting`                                               | sitting                                             | positive | absolute       | 766  | 16842  | sitting                                                          |
| action      | `person/action/squatting`                                             | squatting                                           | positive | absolute       | 4    | 41     | squatting                                                        |
| action      | `person/action/standing`                                              | standing                                            | positive | absolute       | 841  | 25445  | standing                                                         |
| action      | `person/action/taking-a-photo/polarity=negative`                      | not taking a photo                                  | negative | absolute       | 1    | 98     | not taking a photo                                               |
| action      | `person/action/waiting-for-the-traffic-light`                         | waiting for the traffic light                       | positive | absolute       | 10   | 197    | waiting for the traffic light                                    |
| action      | `person/action/walking`                                               | walking                                             | positive | absolute       | 769  | 16241  | walking                                                          |
| action      | `person/action/watching`                                              | watching                                            | positive | absolute       | 6    | 727    | watching                                                         |
| action      | `person/action/watching-the-performance`                              | watching the performance                            | positive | absolute       | 1    | 85     | watching the performance                                         |
| age         | `person/age/adult`                                                    | adult                                               | positive | absolute       | 573  | 9058   | adult                                                            |
| age         | `person/age/elderly`                                                  | elderly                                             | positive | absolute       | 572  | 5243   | elderly                                                          |
| age         | `person/age/kid`                                                      | kid                                                 | positive | absolute       | 376  | 784    | kid                                                              |
| clothing    | `person/clothing/black-gown/verb=wearing`                             | wearing black gown                                  | positive | absolute       | 1    | 25     | wearing a black gown                                             |
| clothing    | `person/clothing/black-tank-top/verb=wearing`                         | wearing black tank top                              | positive | absolute       | 1    | 18     | wearing a black tank top                                         |
| clothing    | `person/clothing/blue-coat/verb=wearing`                              | wearing blue coat                                   | positive | absolute       | 4    | 223    | wearing a blue coat                                              |
| clothing    | `person/clothing/blue-costume/verb=wearing`                           | wearing blue costume                                | positive | absolute       | 3    | 361    | wearing a blue costume                                           |
| clothing    | `person/clothing/blue-dress/verb=wearing`                             | wearing blue dress                                  | positive | absolute       | 1    | 49     | wearing a blue dress                                             |
| clothing    | `person/clothing/blue-or-green-uniform/verb=wearing`                  | wearing blue or green uniform                       | positive | absolute       | 1    | 64     | in blue or green uniform                                         |
| clothing    | `person/clothing/blue-raincoat/verb=wearing`                          | wearing blue raincoat                               | positive | absolute       | 1    | 79     | wearing a blue raincoat                                          |
| clothing    | `person/clothing/blue-shirt/verb=wearing`                             | wearing blue shirt                                  | positive | absolute       | 6    | 1048   | wearing a blue shirt, wearing blue shirt                         |
| clothing    | `person/clothing/blue-skirt/verb=wearing`                             | wearing blue skirt                                  | positive | absolute       | 1    | 6      | wearing a blue skirt                                             |
| clothing    | `person/clothing/blue-uniform/verb=wearing`                           | wearing blue uniform                                | positive | absolute       | 3    | 139    | in blue uniform                                                  |
| clothing    | `person/clothing/green/verb=wearing`                                  | wearing green                                       | positive | absolute       | 1    | 23     | wearing green                                                    |
| clothing    | `person/clothing/green-coat/verb=wearing`                             | wearing green coat                                  | positive | absolute       | 1    | 39     | wearing a green coat                                             |
| clothing    | `person/clothing/green-costume/verb=wearing`                          | wearing green costume                               | positive | absolute       | 2    | 15     | wearing a green costume                                          |
| clothing    | `person/clothing/green-shirt/verb=wearing`                            | wearing green shirt                                 | positive | absolute       | 5    | 502    | wearing a green shirt, wearing green shirt                       |
| clothing    | `person/clothing/green-uniform/verb=wearing`                          | wearing green uniform                               | positive | absolute       | 3    | 177    | in green uniform                                                 |
| clothing    | `person/clothing/jacket/verb=wearing`                                 | wearing jacket                                      | positive | absolute       | 1    | 4      | wearing a jacket                                                 |
| clothing    | `person/clothing/maroon/verb=wearing`                                 | wearing maroon                                      | positive | absolute       | 2    | 42     | wearing maroon                                                   |
| clothing    | `person/clothing/orange-coat/verb=wearing`                            | wearing orange coat                                 | positive | absolute       | 1    | 30     | wearing a orange coat                                            |
| clothing    | `person/clothing/red-coat/verb=wearing`                               | wearing red coat                                    | positive | absolute       | 4    | 316    | wearing a red coat                                               |
| clothing    | `person/clothing/red-costume/verb=wearing`                            | wearing red costume                                 | positive | absolute       | 5    | 495    | wearing a red costume                                            |
| clothing    | `person/clothing/red-dress/verb=wearing`                              | wearing red dress                                   | positive | absolute       | 1    | 17     | wearing a red dress                                              |
| clothing    | `person/clothing/red-gown/verb=wearing`                               | wearing red gown                                    | positive | absolute       | 1    | 25     | wearing a red gown                                               |
| clothing    | `person/clothing/red-uniform/verb=wearing`                            | wearing red uniform                                 | positive | absolute       | 3    | 309    | in red uniform                                                   |
| clothing    | `person/clothing/rugby-uniform/verb=wearing`                          | wearing rugby uniform                               | positive | absolute       | 1    | 23     | in rugby uniform                                                 |
| clothing    | `person/clothing/safety-vest/verb=wearing/polarity=negative`          | not wearing safety vest                             | negative | absolute       | 1    | 81     | not wearing a safety vest                                        |
| clothing    | `person/clothing/safety-vest/verb=wearing`                            | wearing safety vest                                 | positive | absolute       | 1    | 44     | wearing a safety vest                                            |
| clothing    | `person/clothing/striped-shirt/verb=wearing`                          | wearing striped shirt                               | positive | absolute       | 1    | 6      | wearing a striped shirt                                          |
| clothing    | `person/clothing/white-coat/verb=wearing`                             | wearing white coat                                  | positive | absolute       | 1    | 17     | wearing a white coat                                             |
| clothing    | `person/clothing/white-shirt/verb=wearing`                            | wearing white shirt                                 | positive | absolute       | 7    | 762    | wearing a white shirt                                            |
| clothing    | `person/clothing/white-sportswear/verb=wearing`                       | wearing white sportswear                            | positive | absolute       | 1    | 16     | wearing white sportswear                                         |
| clothing    | `person/clothing/white-uniform/verb=wearing`                          | wearing white uniform                               | positive | absolute       | 6    | 321    | in white uniform                                                 |
| clothing    | `person/clothing/yellow-coat/verb=wearing`                            | wearing yellow coat                                 | positive | absolute       | 2    | 124    | wearing a yellow coat                                            |
| clothing    | `person/clothing/yellow-dress/verb=wearing`                           | wearing yellow dress                                | positive | absolute       | 1    | 33     | wearing a yellow dress                                           |
| clothing    | `person/clothing/yellow-raincoat/verb=wearing`                        | wearing yellow raincoat                             | positive | absolute       | 1    | 49     | wearing a yellow raincoat                                        |
| clothing    | `person/clothing/yellow-shirt/verb=wearing`                           | wearing yellow shirt                                | positive | absolute       | 5    | 408    | wearing a yellow shirt                                           |
| clothing    | `person/clothing/yellow-tank-top/verb=wearing`                        | wearing yellow tank top                             | positive | absolute       | 1    | 9      | wearing a yellow tank top                                        |
| clothing    | `person/clothing/yellow-uniform/verb=wearing`                         | wearing yellow uniform                              | positive | absolute       | 2    | 83     | in yellow uniform                                                |
| clothing    | `person/clothing/yukata/verb=wearing/polarity=negative`               | not wearing yukata                                  | negative | absolute       | 1    | 7      | not wearing a yukata                                             |
| clothing    | `person/clothing/yukata/verb=wearing`                                 | wearing yukata                                      | positive | absolute       | 1    | 4      | wearing a yukata                                                 |
| gender      | `person/gender/female`                                                | female                                              | positive | absolute       | 603  | 10405  | female                                                           |
| gender      | `person/gender/male`                                                  | male                                                | positive | absolute       | 603  | 4710   | male                                                             |
| location    | `person/location/at-the-counter`                                      | at the counter                                      | positive | absolute       | 1    | 7      | at the counter                                                   |
| location    | `person/location/in-the-chair/verb=sitting`                           | sitting in the chair                                | positive | absolute       | 7    | 783    | sitting in a chair                                               |
| location    | `person/location/in-the-farthest-back-row/verb=standing`              | standing in the farthest back row                   | positive | scene_relative | 2    | 34     | standing in the farthest back row                                |
| location    | `person/location/in-the-farthest-queue`                               | in the farthest queue                               | positive | scene_relative | 1    | 26     | in the farthest queue                                            |
| location    | `person/location/in-the-first-front-row/verb=sitting`                 | sitting in the first front row                      | positive | scene_relative | 2    | 27     | sitting in the first front row                                   |
| location    | `person/location/in-the-first-front-row/verb=standing`                | standing in the first front row                     | positive | scene_relative | 1    | 17     | standing in the first front row                                  |
| location    | `person/location/in-the-first-left-queue`                             | in the first left queue                             | positive | scene_relative | 1    | 117    | in the first left queue                                          |
| location    | `person/location/in-the-first-right-queue`                            | in the first right queue                            | positive | scene_relative | 1    | 49     | in the first right queue                                         |
| location    | `person/location/in-the-first-row`                                    | in the first row                                    | positive | scene_relative | 1    | 12     | in the first row                                                 |
| location    | `person/location/in-the-first-two-rows/verb=sitting`                  | sitting in the first two rows                       | positive | scene_relative | 1    | 16     | sitting in the first two rows                                    |
| location    | `person/location/in-the-frame`                                        | in the frame                                        | positive | absolute       | 1    | 5      | in the frame                                                     |
| location    | `person/location/in-the-front-two-rows`                               | in the front two rows                               | positive | scene_relative | 2    | 31     | in the front two rows                                            |
| location    | `person/location/in-the-highest-row/verb=standing`                    | standing in the highest row                         | positive | absolute       | 1    | 58     | standing in the highest row                                      |
| location    | `person/location/in-the-left-two-lines`                               | in the left two lines                               | positive | scene_relative | 2    | 81     | in the left two lines                                            |
| location    | `person/location/in-the-leftmost-section`                             | in the leftmost section                             | positive | scene_relative | 1    | 75     | in the leftmost section                                          |
| location    | `person/location/in-the-lowest-row/verb=sitting`                      | sitting in the lowest row                           | positive | absolute       | 1    | 46     | sitting in the lowest row                                        |
| location    | `person/location/in-the-middle-two-lines`                             | in the middle two lines                             | positive | scene_relative | 3    | 91     | in the middle two lines                                          |
| location    | `person/location/in-the-nearest-queue`                                | in the nearest queue                                | positive | scene_relative | 1    | 33     | in the nearest queue                                             |
| location    | `person/location/in-the-queue/polarity=negative`                      | not in the queue                                    | negative | absolute       | 4    | 184    | not in a queue                                                   |
| location    | `person/location/in-the-queue`                                        | in the queue                                        | positive | absolute       | 89   | 9545   | in queue, in the queue, in a queue                               |
| location    | `person/location/in-the-queue-to-order`                               | in the queue to order                               | positive | absolute       | 1    | 10     | in the queue to order                                            |
| location    | `person/location/in-the-right-two-lines`                              | in the right two lines                              | positive | scene_relative | 1    | 23     | in the right two lines                                           |
| location    | `person/location/in-the-rightmost-line`                               | in the rightmost line                               | positive | scene_relative | 2    | 34     | in the rightmost line                                            |
| location    | `person/location/in-the-rightmost-section`                            | in the rightmost section                            | positive | scene_relative | 1    | 39     | in the rightmost section                                         |
| location    | `person/location/in-the-rightmost-section/verb=sitting`               | sitting in the rightmost section                    | positive | scene_relative | 1    | 27     | sitting in the rightmost section                                 |
| location    | `person/location/in-the-sea`                                          | in the sea                                          | positive | absolute       | 4    | 95     | in the sea                                                       |
| location    | `person/location/in-the-second-front-row/verb=sitting`                | sitting in the second front row                     | positive | scene_relative | 1    | 8      | sitting in the second front row                                  |
| location    | `person/location/in-the-second-front-row/verb=standing`               | standing in the second front row                    | positive | scene_relative | 1    | 22     | standing in the second front row                                 |
| location    | `person/location/in-the-second-left-section/verb=sitting`             | sitting in the second left section                  | positive | scene_relative | 1    | 52     | sitting in the second left section                               |
| location    | `person/location/in-the-sky`                                          | in the sky                                          | positive | absolute       | 1    | 1      | in the sky                                                       |
| location    | `person/location/in-the-stands`                                       | in the stands                                       | positive | absolute       | 2    | 335    | in the stands                                                    |
| location    | `person/location/in-the-third-front-row`                              | in the third front row                              | positive | scene_relative | 1    | 8      | in the third front row                                           |
| location    | `person/location/in-the-third-left-queue`                             | in the third left queue                             | positive | scene_relative | 1    | 108    | in the third left queue                                          |
| location    | `person/location/in-the-third-row/verb=sitting`                       | sitting in the third row                            | positive | scene_relative | 1    | 14     | sitting in the third row                                         |
| location    | `person/location/inside-the-bar-counter`                              | inside the bar counter                              | positive | absolute       | 1    | 3      | inside the bar counter                                           |
| location    | `person/location/on-the-beach`                                        | on the beach                                        | positive | absolute       | 4    | 156    | on the beach                                                     |
| location    | `person/location/on-the-bottom-left-side-walk`                        | on the bottom left side walk                        | positive | scene_relative | 30   | 350    | on the bottom left side walk                                     |
| location    | `person/location/on-the-bottom-right-side-walk`                       | on the bottom right side walk                       | positive | scene_relative | 30   | 602    | on the bottom right side walk                                    |
| location    | `person/location/on-the-bottom-zebra-crossing`                        | on the bottom zebra crossing                        | positive | absolute       | 1    | 22     | on the bottom zebra crossing                                     |
| location    | `person/location/on-the-bridge`                                       | on the bridge                                       | positive | absolute       | 2    | 51     | on the bridge                                                    |
| location    | `person/location/on-the-chair/verb=sitting`                           | sitting on the chair                                | positive | absolute       | 3    | 146    | sitting on the chair                                             |
| location    | `person/location/on-the-elevator/polarity=negative`                   | not on the elevator                                 | negative | absolute       | 1    | 192    | not on the elevator                                              |
| location    | `person/location/on-the-elevator`                                     | on the elevator                                     | positive | absolute       | 1    | 14     | on the elevator                                                  |
| location    | `person/location/on-the-escalator/polarity=negative`                  | not on the escalator                                | negative | absolute       | 1    | 88     | not on the escalator                                             |
| location    | `person/location/on-the-escalator`                                    | on the escalator                                    | positive | absolute       | 3    | 55     | on the escalator                                                 |
| location    | `person/location/on-the-first-floor`                                  | on the first floor                                  | positive | scene_relative | 1    | 63     | on the first floor                                               |
| location    | `person/location/on-the-ground`                                       | on the ground                                       | positive | absolute       | 3    | 113    | on the ground                                                    |
| location    | `person/location/on-the-ground/verb=sitting`                          | sitting on the ground                               | positive | absolute       | 1    | 17     | sitting on the ground                                            |
| location    | `person/location/on-the-ground-floor`                                 | on the ground floor                                 | positive | absolute       | 1    | 128    | on the ground floor                                              |
| location    | `person/location/on-the-ground-level`                                 | on the ground level                                 | positive | absolute       | 2    | 76     | on the ground level                                              |
| location    | `person/location/on-the-left-side-of-a-red-carpet/verb=standing`      | standing on the left side of a red carpet           | positive | absolute       | 1    | 43     | standing on the left side of a red carpet                        |
| location    | `person/location/on-the-left-side-of-the-barrier`                     | on the left side of the barrier                     | positive | absolute       | 1    | 21     | on the left side of the barrier                                  |
| location    | `person/location/on-the-left-side-of-the-table/verb=sitting`          | sitting on the left side of the table               | positive | absolute       | 1    | 5      | sitting on the left side of the table                            |
| location    | `person/location/on-the-playground/verb=walking`                      | walking on the playground                           | positive | absolute       | 1    | 63     | walking on the playground                                        |
| location    | `person/location/on-the-red-chairs/verb=sitting`                      | sitting on the red chairs                           | positive | absolute       | 1    | 99     | sitting on red chairs                                            |
| location    | `person/location/on-the-right-side-of-a-red-carpet/verb=standing`     | standing on the right side of a red carpet          | positive | absolute       | 1    | 39     | standing on the right side of a red carpet                       |
| location    | `person/location/on-the-right-side-of-the-barrier`                    | on the right side of the barrier                    | positive | absolute       | 1    | 15     | on the right side of the barrier                                 |
| location    | `person/location/on-the-right-side-of-the-biggest-table/verb=sitting` | sitting on the right side of the biggest table      | positive | absolute       | 1    | 8      | sitting on the right side of the biggest table                   |
| location    | `person/location/on-the-rightmost-escalator`                          | on the rightmost escalator                          | positive | scene_relative | 1    | 24     | on the rightmost escalator                                       |
| location    | `person/location/on-the-road`                                         | on the road                                         | positive | absolute       | 1    | 6      | on the road                                                      |
| location    | `person/location/on-the-second-floor`                                 | on the second floor                                 | positive | scene_relative | 2    | 21     | on the second floor                                              |
| location    | `person/location/on-the-second-left-escalator`                        | on the second left escalator                        | positive | scene_relative | 1    | 14     | on the second left escalator                                     |
| location    | `person/location/on-the-second-level`                                 | on the second level                                 | positive | scene_relative | 1    | 40     | on the second level                                              |
| location    | `person/location/on-the-side/verb=walking`                            | walking on the side                                 | positive | absolute       | 9    | 70     | walk by on the side, walking on the side, walking by on the side |
| location    | `person/location/on-the-sidewalk`                                     | on the sidewalk                                     | positive | absolute       | 4    | 79     | on the sidewalk                                                  |
| location    | `person/location/on-the-sidewalk/verb=walking`                        | walking on the sidewalk                             | positive | absolute       | 1    | 35     | walking on the sidewalk                                          |
| location    | `person/location/on-the-stage/polarity=negative`                      | not on the stage                                    | negative | absolute       | 1    | 52     | not on the stage                                                 |
| location    | `person/location/on-the-stage`                                        | on the stage                                        | positive | absolute       | 1    | 60     | on the stage                                                     |
| location    | `person/location/on-the-stairs`                                       | on the stairs                                       | positive | absolute       | 1    | 62     | on the stairs                                                    |
| location    | `person/location/on-the-stairs/verb=sitting`                          | sitting on the stairs                               | positive | absolute       | 4    | 127    | sitting on the stairs, sitting on stairs                         |
| location    | `person/location/on-the-top-side-walk`                                | on the top side walk                                | positive | scene_relative | 30   | 1579   | on the top side walk                                             |
| location    | `person/location/on-the-wall/verb=sitting`                            | sitting on the wall                                 | positive | absolute       | 1    | 16     | sitting on the wall                                              |
| location    | `person/location/on-the-zebra-crossing-from-bottom-left-to-top-right` | on the zebra crossing from bottom left to top right | positive | absolute       | 1    | 13     | on the zebra crossing from bottom left to top right              |
| location    | `person/location/outside-the-frame`                                   | outside the frame                                   | positive | absolute       | 1    | 1      | outside the frame                                                |
| location    | `person/location/performing-on-the-stage`                             | performing on the stage                             | positive | absolute       | 1    | 10     | performing on the stage                                          |
| location    | `person/location/queuing-in-the-nearest-line`                         | queuing in the nearest line                         | positive | scene_relative | 1    | 8      | queuing in the nearest line                                      |
| location    | `person/location/queuing-in-the-second-nearest-line`                  | queuing in the second nearest line                  | positive | scene_relative | 1    | 15     | queuing in the second nearest line                               |
| location    | `person/location/squatting-in-the-first-front-row`                    | squatting in the first front row                    | positive | scene_relative | 1    | 12     | squatting in the first front row                                 |
| location    | `person/location/standing-behind-the-wall`                            | standing behind the wall                            | positive | absolute       | 1    | 57     | standing behind the wall                                         |
| location    | `person/location/to-the-left-of-belt-barrier`                         | to the left of belt barrier                         | positive | absolute       | 1    | 22     | to the left of belt barrier                                      |
| location    | `person/location/to-the-right-of-belt-barrier`                        | to the right of belt barrier                        | positive | absolute       | 1    | 280    | to the right of belt barrier                                     |
| location    | `person/location/watching-in-the-middle-stands`                       | watching in the middle stands                       | positive | absolute       | 1    | 68     | watching in the middle stands                                    |
| orientation | `person/orientation/approaching`                                      | approaching                                         | positive | absolute       | 34   | 883    | approaching                                                      |
| orientation | `person/orientation/away/verb=walking`                                | walking away                                        | positive | absolute       | 34   | 720    | walking away                                                     |
| orientation | `person/orientation/downward/verb=walking`                            | walking downward                                    | positive | absolute       | 4    | 69     | walking downward                                                 |
| orientation | `person/orientation/from-left-to-right/verb=walking`                  | walking from left to right                          | positive | absolute       | 1    | 21     | walking from left to right                                       |
| orientation | `person/orientation/from-right-to-left/verb=walking`                  | walking from right to left                          | positive | absolute       | 1    | 26     | walking from right to left                                       |
| orientation | `person/orientation/to-the-left/verb=walking`                         | walking to the left                                 | positive | absolute       | 32   | 704    | walking to the left                                              |
| orientation | `person/orientation/to-the-right/verb=walking`                        | walking to the right                                | positive | absolute       | 32   | 631    | walking to the right                                             |
| orientation | `person/orientation/upward/verb=walking`                              | walking upward                                      | positive | absolute       | 4    | 69     | walking upward                                                   |

## pill

| 维度     | 谓词 id                                             | 写法                                | 极性     | 作用域         | 根数 | 实例数 | 出现写法                            |
|----------|-----------------------------------------------------|-------------------------------------|----------|----------------|------|--------|-------------------------------------|
| color    | `pill/color/blue`                                   | blue                                | positive | absolute       | 2    | 55     | blue                                |
| color    | `pill/color/in-yellowish-orange`                    | in yellowish-orange                 | positive | absolute       | 1    | 21     | in yellowish-orange                 |
| color    | `pill/color/pink`                                   | pink                                | positive | absolute       | 1    | 7      | pink                                |
| color    | `pill/color/purple`                                 | purple                              | positive | absolute       | 1    | 7      | purple                              |
| color    | `pill/color/red`                                    | red                                 | positive | absolute       | 2    | 70     | red                                 |
| color    | `pill/color/white`                                  | white                               | positive | absolute       | 4    | 148    | white                               |
| color    | `pill/color/yellow`                                 | yellow                              | positive | absolute       | 1    | 26     | yellow                              |
| location | `pill/location/in-the-blister-pack`                 | in the blister pack                 | positive | absolute       | 1    | 25     | in the blister pack                 |
| location | `pill/location/in-the-rightmost-stack`              | in the rightmost stack              | positive | scene_relative | 1    | 5      | in the rightmost stack              |
| location | `pill/location/in-the-third-and-fourth-left-stacks` | in the third and fourth left stacks | positive | scene_relative | 1    | 7      | in the third and fourth left stacks |
| location | `pill/location/scattered-on-the-table`              | scattered on the table              | positive | absolute       | 1    | 18     | scattered on the table              |

## plate

| 维度     | 谓词 id                                 | 写法                   | 极性     | 作用域         | 根数 | 实例数 | 出现写法               |
|----------|-----------------------------------------|------------------------|----------|----------------|------|--------|------------------------|
| color    | `plate/color/black`                     | black                  | positive | absolute       | 2    | 8      | black                  |
| color    | `plate/color/blue`                      | blue                   | positive | absolute       | 1    | 37     | blue                   |
| color    | `plate/color/orange`                    | orange                 | positive | absolute       | 1    | 13     | orange                 |
| color    | `plate/color/yellow`                    | yellow                 | positive | absolute       | 1    | 2      | yellow                 |
| location | `plate/location/in-the-frontmost-stack` | in the frontmost stack | positive | absolute       | 1    | 14     | in the frontmost stack |
| location | `plate/location/in-the-leftmost-stack`  | in the leftmost stack  | positive | scene_relative | 1    | 17     | in the leftmost stack  |

## pocket

| 维度     | 谓词 id                                      | 写法                       | 极性     | 作用域   | 根数 | 实例数 | 出现写法                   |
|----------|----------------------------------------------|----------------------------|----------|----------|------|--------|----------------------------|
| location | `pocket/location/empty-in-the-blister-pack`  | empty in the blister pack  | positive | absolute | 1    | 2      | empty in the blister pack  |
| location | `pocket/location/filled-in-the-blister-pack` | filled in the blister pack | positive | absolute | 1    | 54     | filled in the blister pack |

## sauce bottle

| 维度     | 谓词 id                                           | 写法                      | 极性     | 作用域         | 根数 | 实例数 | 出现写法                  |
|----------|---------------------------------------------------|---------------------------|----------|----------------|------|--------|---------------------------|
| location | `sauce-bottle/location/on-the-first-bottom-shelf` | on the first bottom shelf | positive | scene_relative | 1    | 15     | on the first bottom shelf |
| location | `sauce-bottle/location/on-the-first-top-shelf`    | on the first top shelf    | positive | scene_relative | 1    | 16     | on the first top shelf    |
| location | `sauce-bottle/location/on-the-lower-shelf`        | on the lower shelf        | positive | absolute       | 2    | 24     | on the lower shelf        |
| location | `sauce-bottle/location/on-the-middle-shelf`       | on the middle shelf       | positive | absolute       | 1    | 17     | on the middle shelf       |
| location | `sauce-bottle/location/on-the-top-shelf`          | on the top shelf          | positive | absolute       | 2    | 25     | on the top shelf          |

## shirt

| 维度     | 谓词 id                                         | 写法                           | 极性     | 作用域         | 根数 | 实例数 | 出现写法                       |
|----------|-------------------------------------------------|--------------------------------|----------|----------------|------|--------|--------------------------------|
| color    | `shirt/color/purple`                            | purple                         | positive | absolute       | 1    | 34     | purple                         |
| color    | `shirt/color/yellow`                            | yellow                         | positive | absolute       | 1    | 35     | yellow                         |
| location | `shirt/location/blue-in-the-third-top-shelf`    | blue in the third top shelf    | positive | scene_relative | 1    | 4      | blue in the third top shelf    |
| location | `shirt/location/in-the-bottom-left-compartment` | in the bottom left compartment | positive | absolute       | 1    | 21     | in the bottom left compartment |
| location | `shirt/location/in-the-left-top-compartment`    | in the left top compartment    | positive | absolute       | 1    | 13     | in the left top compartment    |
| location | `shirt/location/in-the-right-top-compartment`   | in the right top compartment   | positive | absolute       | 1    | 14     | in the right top compartment   |
| location | `shirt/location/on-the-bottom-layer`            | on the bottom layer            | positive | absolute       | 1    | 28     | on the bottom layer            |
| location | `shirt/location/on-the-lowest-shelf`            | on the lowest shelf            | positive | absolute       | 1    | 34     | on the lowest shelf            |
| location | `shirt/location/on-the-middle-shelf`            | on the middle shelf            | positive | absolute       | 1    | 32     | on the middle shelf            |
| location | `shirt/location/on-the-top-layer`               | on the top layer               | positive | absolute       | 1    | 41     | on the top layer               |
| location | `shirt/location/red-in-the-second-bottom-shelf` | red in the second bottom shelf | positive | scene_relative | 1    | 8      | red in the second bottom shelf |

## shoe

| 维度     | 谓词 id                           | 写法              | 极性     | 作用域   | 根数 | 实例数 | 出现写法          |
|----------|-----------------------------------|-------------------|----------|----------|------|--------|-------------------|
| color    | `shoe/color/black`                | black             | positive | absolute | 1    | 16     | black             |
| color    | `shoe/color/blue`                 | blue              | positive | absolute | 1    | 8      | blue              |
| color    | `shoe/color/white`                | white             | positive | absolute | 1    | 8      | white             |
| location | `shoe/location/on-the-back-row`   | on the back row   | positive | absolute | 1    | 18     | on the back row   |
| location | `shoe/location/on-the-front-row`  | on the front row  | positive | absolute | 1    | 16     | on the front row  |
| location | `shoe/location/on-the-middle-row` | on the middle row | positive | absolute | 1    | 18     | on the middle row |

## shoe box

| 维度     | 谓词 id                                                  | 写法                                 | 极性     | 作用域         | 根数 | 实例数 | 出现写法                             |
|----------|----------------------------------------------------------|--------------------------------------|----------|----------------|------|--------|--------------------------------------|
| color    | `shoe-box/color/black`                                   | black                                | positive | absolute       | 1    | 21     | black                                |
| color    | `shoe-box/color/blue`                                    | blue                                 | positive | absolute       | 1    | 4      | blue                                 |
| color    | `shoe-box/color/red`                                     | red                                  | positive | absolute       | 1    | 6      | red                                  |
| location | `shoe-box/location/in-the-leftmost-three-stacks`         | in the leftmost three stacks         | positive | scene_relative | 2    | 79     | in the leftmost three stacks         |
| location | `shoe-box/location/in-the-leftmost-two-stacks`           | in the leftmost two stacks           | positive | scene_relative | 1    | 16     | in the leftmost two stacks           |
| location | `shoe-box/location/in-the-rightmost-stack`               | in the rightmost stack               | positive | scene_relative | 1    | 6      | in the rightmost stack               |
| location | `shoe-box/location/in-the-rightmost-two-stacks`          | in the rightmost two stacks          | positive | scene_relative | 1    | 29     | in the rightmost two stacks          |
| location | `shoe-box/location/in-the-second-and-third-right-stacks` | in the second and third right stacks | positive | scene_relative | 1    | 15     | in the second and third right stacks |

## skateboard

| 维度     | 谓词 id                                 | 写法              | 极性     | 作用域   | 根数 | 实例数 | 出现写法          |
|----------|-----------------------------------------|-------------------|----------|----------|------|--------|-------------------|
| color    | `skateboard/color/black`                | black             | positive | absolute | 1    | 10     | black             |
| color    | `skateboard/color/golden`               | golden            | positive | absolute | 1    | 7      | golden            |
| color    | `skateboard/color/white`                | white             | positive | absolute | 1    | 9      | white             |
| location | `skateboard/location/on-the-left-wall`  | on the left wall  | positive | absolute | 1    | 26     | on the left wall  |
| location | `skateboard/location/on-the-right-wall` | on the right wall | positive | absolute | 1    | 44     | on the right wall |

## sneaker

| 维度     | 谓词 id                                                   | 写法                                   | 极性     | 作用域         | 根数 | 实例数 | 出现写法                               |
|----------|-----------------------------------------------------------|----------------------------------------|----------|----------------|------|--------|----------------------------------------|
| color    | `sneaker/color/blue`                                      | blue                                   | positive | absolute       | 1    | 2      | blue                                   |
| color    | `sneaker/color/red`                                       | red                                    | positive | absolute       | 1    | 8      | red                                    |
| location | `sneaker/location/in-the-bottom-two-rows`                 | in the bottom two rows                 | positive | scene_relative | 1    | 11     | in the bottom two rows                 |
| location | `sneaker/location/in-the-highest-layer`                   | in the highest layer                   | positive | absolute       | 3    | 33     | in the highest layer                   |
| location | `sneaker/location/in-the-second-and-third-highest-layers` | in the second and third highest layers | positive | scene_relative | 1    | 18     | in the second and third highest layers |
| location | `sneaker/location/in-the-second-and-third-lowest-layers`  | in the second and third lowest layers  | positive | scene_relative | 2    | 45     | in the second and third lowest layers  |
| location | `sneaker/location/in-the-second-left-column`              | in the second left column              | positive | scene_relative | 1    | 7      | in the second left column              |
| location | `sneaker/location/in-the-second-top-row`                  | in the second top row                  | positive | scene_relative | 1    | 6      | in the second top row                  |
| location | `sneaker/location/in-the-third-right-column`              | in the third right column              | positive | scene_relative | 1    | 7      | in the third right column              |

## spectacles

| 维度     | 谓词 id                                         | 写法                      | 极性     | 作用域         | 根数 | 实例数 | 出现写法                  |
|----------|-------------------------------------------------|---------------------------|----------|----------------|------|--------|---------------------------|
| location | `spectacles/location/in-the-first-left-column`  | in the first left column  | positive | scene_relative | 1    | 20     | in the first left column  |
| location | `spectacles/location/in-the-first-right-column` | in the first right column | positive | scene_relative | 1    | 21     | in the first right column |

## spoon

| 维度     | 谓词 id                  | 写法    | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|----------|--------------------------|---------|----------|----------|------|--------|----------|
| material | `spoon/material/metal`   | metal   | positive | absolute | 1    | 6      | metal    |
| material | `spoon/material/plastic` | plastic | positive | absolute | 1    | 7      | plastic  |
| material | `spoon/material/wooden`  | wooden  | positive | absolute | 1    | 3      | wooden   |

## stamp

| 维度     | 谓词 id                                  | 写法                    | 极性     | 作用域         | 根数 | 实例数 | 出现写法                |
|----------|------------------------------------------|-------------------------|----------|----------------|------|--------|-------------------------|
| color    | `stamp/color/bluish`                     | bluish                  | positive | absolute       | 1    | 5      | bluish                  |
| color    | `stamp/color/greenish`                   | greenish                | positive | absolute       | 1    | 12     | greenish                |
| color    | `stamp/color/reddish`                    | reddish                 | positive | absolute       | 1    | 12     | reddish                 |
| location | `stamp/location/in-the-first-bottom-row` | in the first bottom row | positive | scene_relative | 1    | 5      | in the first bottom row |
| location | `stamp/location/in-the-top-two-rows`     | in the top two rows     | positive | scene_relative | 1    | 10     | in the top two rows     |

## straw

| 维度  | 谓词 id              | 写法   | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|----------------------|--------|----------|----------|------|--------|----------|
| color | `straw/color/blue`   | blue   | positive | absolute | 4    | 96     | blue     |
| color | `straw/color/cyan`   | cyan   | positive | absolute | 1    | 39     | cyan     |
| color | `straw/color/green`  | green  | positive | absolute | 4    | 87     | green    |
| color | `straw/color/pink`   | pink   | positive | absolute | 1    | 17     | pink     |
| color | `straw/color/red`    | red    | positive | absolute | 3    | 99     | red      |
| color | `straw/color/yellow` | yellow | positive | absolute | 3    | 94     | yellow   |

## strawberry

| 维度     | 谓词 id                                    | 写法                 | 极性     | 作用域   | 根数 | 实例数 | 出现写法             |
|----------|--------------------------------------------|----------------------|----------|----------|------|--------|----------------------|
| location | `strawberry/location/in-the-bowl`          | in the bowl          | positive | absolute | 1    | 14     | in the bowl          |
| location | `strawberry/location/in-the-center-bowl`   | in the center bowl   | positive | absolute | 1    | 13     | in the center bowl   |
| location | `strawberry/location/in-the-left-section`  | in the left section  | positive | absolute | 2    | 130    | in the left section  |
| location | `strawberry/location/in-the-letter-l`      | in the letter l      | positive | absolute | 1    | 6      | in the letter l      |
| location | `strawberry/location/in-the-letter-v`      | in the letter v      | positive | absolute | 1    | 7      | in the letter v      |
| location | `strawberry/location/in-the-right-section` | in the right section | positive | absolute | 2    | 107    | in the right section |
| location | `strawberry/location/in-the-top-left-bowl` | in the top left bowl | positive | absolute | 1    | 10     | in the top left bowl |
| location | `strawberry/location/in-the-top-left-box`  | in the top left box  | positive | absolute | 1    | 16     | in the top left box  |
| location | `strawberry/location/in-the-top-right-box` | in the top right box | positive | absolute | 1    | 14     | in the top right box |
| location | `strawberry/location/on-the-tray`          | on the tray          | positive | absolute | 1    | 4      | on the tray          |

## sunglasses

| 维度     | 谓词 id                                                     | 写法                                  | 极性     | 作用域         | 根数 | 实例数 | 出现写法                              |
|----------|-------------------------------------------------------------|---------------------------------------|----------|----------------|------|--------|---------------------------------------|
| location | `sunglasses/location/in-fourth-and-sixth-top-rows`          | in fourth and sixth top rows          | positive | scene_relative | 1    | 18     | in fourth and sixth top rows          |
| location | `sunglasses/location/in-leftmost-column`                    | in leftmost column                    | positive | scene_relative | 2    | 26     | in leftmost column                    |
| location | `sunglasses/location/in-rightmost-column`                   | in rightmost column                   | positive | scene_relative | 1    | 16     | in rightmost column                   |
| location | `sunglasses/location/in-the-rightmost-column`               | in the rightmost column               | positive | scene_relative | 1    | 10     | in the rightmost column               |
| location | `sunglasses/location/in-the-second-right-column`            | in the second right column            | positive | scene_relative | 1    | 16     | in the second right column            |
| location | `sunglasses/location/in-the-second-row`                     | in the second row                     | positive | scene_relative | 1    | 3      | in the second row                     |
| location | `sunglasses/location/in-the-third-and-seventh-left-columns` | in the third and seventh left columns | positive | scene_relative | 1    | 20     | in the third and seventh left columns |
| location | `sunglasses/location/in-the-third-left-column`              | in the third left column              | positive | scene_relative | 1    | 16     | in the third left column              |
| location | `sunglasses/location/in-the-top-row`                        | in the top row                        | positive | absolute       | 1    | 3      | in the top row                        |

## sushi

| 维度    | 谓词 id                       | 写法          | 极性     | 作用域   | 根数 | 实例数 | 出现写法      |
|---------|-------------------------------|---------------|----------|----------|------|--------|---------------|
| variety | `sushi/variety/with-fish-roe` | with fish roe | positive | absolute | 1    | 11     | with fish roe |
| variety | `sushi/variety/with-seawead`  | with seawead  | positive | absolute | 1    | 21     | with seawead  |

## swan

| 维度     | 谓词 id                      | 写法         | 极性     | 作用域   | 根数 | 实例数 | 出现写法     |
|----------|------------------------------|--------------|----------|----------|------|--------|--------------|
| location | `swan/location/in-the-water` | in the water | positive | absolute | 2    | 27     | in the water |
| location | `swan/location/on-the-bank`  | on the bank  | positive | absolute | 2    | 36     | on the bank  |

## tomato

| 维度     | 谓词 id                        | 写法         | 极性     | 作用域   | 根数 | 实例数 | 出现写法     |
|----------|--------------------------------|--------------|----------|----------|------|--------|--------------|
| color    | `tomato/color/green`           | green        | positive | absolute | 3    | 115    | green        |
| color    | `tomato/color/red`             | red          | positive | absolute | 7    | 224    | red          |
| color    | `tomato/color/yellow`          | yellow       | positive | absolute | 4    | 57     | yellow       |
| location | `tomato/location/in-the-bowl`  | in the bowl  | positive | absolute | 2    | 26     | in the bowl  |
| location | `tomato/location/on-the-table` | on the table | positive | absolute | 2    | 10     | on the table |

## vegetable

| 维度     | 谓词 id                           | 写法         | 极性     | 作用域   | 根数 | 实例数 | 出现写法     |
|----------|-----------------------------------|--------------|----------|----------|------|--------|--------------|
| location | `vegetable/location/on-the-table` | on the table | positive | absolute | 1    | 9      | on the table |

## wall

| 维度  | 谓词 id           | 写法 | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|-------------------|------|----------|----------|------|--------|----------|
| color | `wall/color/blue` | blue | positive | absolute | 1    | 12     | blue     |
| color | `wall/color/red`  | red  | positive | absolute | 1    | 12     | red      |

## window

| 维度  | 谓词 id              | 写法  | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|----------------------|-------|----------|----------|------|--------|----------|
| color | `window/color/brown` | brown | positive | absolute | 1    | 5      | brown    |
| color | `window/color/white` | white | positive | absolute | 1    | 10     | white    |

## window frame

| 维度  | 谓词 id                     | 写法   | 极性     | 作用域   | 根数 | 实例数 | 出现写法 |
|-------|-----------------------------|--------|----------|----------|------|--------|----------|
| color | `window-frame/color/red`    | red    | positive | absolute | 1    | 12     | red      |
| color | `window-frame/color/yellow` | yellow | positive | absolute | 1    | 40     | yellow   |

## wine bottle

| 维度     | 谓词 id                                                        | 写法                                    | 极性     | 作用域         | 根数 | 实例数 | 出现写法                                |
|----------|----------------------------------------------------------------|-----------------------------------------|----------|----------------|------|--------|-----------------------------------------|
| location | `wine-bottle/location/in-the-third-top-and-middle-compartment` | in the third top and middle compartment | positive | scene_relative | 1    | 12     | in the third top and middle compartment |
| location | `wine-bottle/location/in-the-topmost-and-middle-compartment`   | in the topmost and middle compartment   | positive | scene_relative | 1    | 10     | in the topmost and middle compartment   |
| location | `wine-bottle/location/on-the-bottom-shelf`                     | on the bottom shelf                     | positive | absolute       | 1    | 14     | on the bottom shelf                     |
| location | `wine-bottle/location/on-the-highest-shelf`                    | on the highest shelf                    | positive | absolute       | 2    | 69     | on the highest shelf                    |
| location | `wine-bottle/location/on-the-lowest-shelf`                     | on the lowest shelf                     | positive | absolute       | 1    | 28     | on the lowest shelf                     |
| location | `wine-bottle/location/on-the-middle-shelf`                     | on the middle shelf                     | positive | absolute       | 1    | 16     | on the middle shelf                     |
| location | `wine-bottle/location/on-the-second-lowest-shelf`              | on the second lowest shelf              | positive | scene_relative | 1    | 61     | on the second lowest shelf              |
| location | `wine-bottle/location/on-the-third-and-fourth-top-shelves`     | on the third and fourth top shelves     | positive | scene_relative | 1    | 140    | on the third and fourth top shelves     |
| location | `wine-bottle/location/on-the-top-shelf`                        | on the top shelf                        | positive | absolute       | 1    | 13     | on the top shelf                        |
| location | `wine-bottle/location/on-the-topmost-shelf`                    | on the topmost shelf                    | positive | scene_relative | 1    | 58     | on the topmost shelf                    |
